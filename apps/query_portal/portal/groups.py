"""Group membership, resolved as the user on the warehouse.

`is_account_group_member` is evaluated with the end user's token, so it reports
exactly the membership Unity Catalog itself uses for grants — no account-admin
entitlement and no second source of truth.

All groups referenced anywhere in the catalog are tested in a single round trip,
because a 40-query catalog would otherwise cost 40 warehouse calls on page load.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

from portal import sql
from portal.config import Settings
from portal.metadata import QueryDefinition, rows_as_dicts


def referenced_groups(queries: Sequence[QueryDefinition]) -> list[str]:
    """Distinct, order-stable groups mentioned by any query in the catalog."""
    seen: dict[str, None] = {}
    for query in queries:
        for group in query.allowed_groups:
            name = (group or "").strip()
            if name:
                seen.setdefault(name, None)
    return list(seen)


def resolve_membership(
    user_client: WorkspaceClient,
    settings: Settings,
    groups: Iterable[str],
) -> set[str]:
    """Return the subset of `groups` the calling user belongs to.

    Runs with the *user's* client. Group names bind as parameters; only their
    count shapes the statement text.
    """
    names = [g.strip() for g in groups if g and g.strip()]
    if not names:
        return set()

    parameters = [
        StatementParameterListItem(name=f"g{i}", value=name, type="STRING")
        for i, name in enumerate(names)
    ]
    response = user_client.statement_execution.execute_statement(
        statement=sql.group_membership(len(names)),
        warehouse_id=settings.warehouse_id,
        parameters=parameters,
        wait_timeout="30s",
    )

    member_of: set[str] = set()
    for row in rows_as_dicts(response):
        if str(row.get("is_member")).strip().lower() in {"true", "t", "1"}:
            member_of.add(str(row.get("group_name")))
    return member_of


class GroupResolver:
    """Caches membership for the lifetime of a session."""

    def __init__(self, user_client: WorkspaceClient, settings: Settings):
        self._client = user_client
        self._settings = settings
        self._cache: dict[str, bool] = {}
        # Set when membership could not be resolved, so the UI can explain why a
        # restricted query is missing instead of leaving the user guessing.
        self.last_error: str | None = None

    def membership(self, groups: Iterable[str]) -> set[str]:
        names = [g.strip() for g in groups if g and g.strip()]
        unknown = [n for n in names if n not in self._cache]
        if unknown:
            try:
                resolved = resolve_membership(self._client, self._settings, unknown)
            except Exception as exc:  # noqa: BLE001 - degrade to "no membership"
                # Fail closed: an unresolvable group hides its queries rather than
                # revealing them. Nothing is exposed either way, because Unity
                # Catalog still gates the data.
                self.last_error = f"{type(exc).__name__}: {exc}"
                resolved = set()
            for name in unknown:
                self._cache[name] = name in resolved
        return {n for n in names if self._cache.get(n)}


def visible_for_user(
    user_client: WorkspaceClient,
    settings: Settings,
    queries: Sequence[QueryDefinition],
    resolver: GroupResolver | None = None,
) -> list[QueryDefinition]:
    """Apply `allowed_groups` filtering, honouring the `group_filtering` toggle.

    With filtering off (the Free Edition default path, where account groups
    cannot exist) every query is listed. That is a UX decision, not a security
    one: Unity Catalog grants still decide what the user can actually read.
    """
    from portal.metadata import visible_queries

    if not settings.group_filtering:
        return list(queries)

    groups = referenced_groups(queries)
    if not groups:
        return list(queries)

    resolver = resolver or GroupResolver(user_client, settings)
    return visible_queries(queries, resolver.membership(groups))
