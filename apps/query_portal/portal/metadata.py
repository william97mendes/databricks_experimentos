"""Metadata model and loader.

Publishing a query is an INSERT into `query_catalog` plus rows in
`query_parameter`. No code change, no deploy. These reads use the *service
principal* — business users are not granted access to the metadata tables — while
every published query they launch runs as themselves.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

from portal import sql
from portal.config import Settings
from portal.errors import PortalError, ValidationError
from portal.params import ParamType


@dataclass(frozen=True)
class QueryParameter:
    query_id: str
    param_name: str
    label: str
    param_type: ParamType
    sql_type: str | None = None
    help_text: str | None = None
    is_required: bool = False
    default_value: str | None = None
    options_sql: str | None = None
    options_static: list[str] = field(default_factory=list)
    max_range_days: int | None = None
    display_order: int = 0


@dataclass(frozen=True)
class QueryDefinition:
    query_id: str
    title: str
    sql_template: str
    description: str | None = None
    category: str | None = None
    allowed_groups: list[str] = field(default_factory=list)
    warehouse_id: str | None = None
    max_rows: int | None = None
    timeout_seconds: int | None = None
    owner_email: str | None = None
    parameters: list[QueryParameter] = field(default_factory=list)

    @property
    def is_public(self) -> bool:
        """No groups listed means every employee sees it in the list."""
        return not self.allowed_groups

    def effective_warehouse(self, settings: Settings) -> str:
        return (self.warehouse_id or "").strip() or settings.warehouse_id

    def effective_max_rows(self, settings: Settings) -> int:
        return self.max_rows or settings.default_max_rows

    def effective_timeout(self, settings: Settings) -> int:
        return self.timeout_seconds or settings.default_timeout_seconds


# --------------------------------------------------------------------------- #
# Result parsing
# --------------------------------------------------------------------------- #


def rows_as_dicts(response: Any) -> list[dict[str, Any]]:
    """Turn an INLINE StatementResponse into dicts keyed by column name.

    The API returns every value as a string (or None), so callers coerce.
    """
    manifest = getattr(response, "manifest", None)
    schema = getattr(manifest, "schema", None)
    columns = getattr(schema, "columns", None) or []
    names = [c.name for c in columns]

    result = getattr(response, "result", None)
    data = getattr(result, "data_array", None) or []
    return [dict(zip(names, row, strict=False)) for row in data]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "t", "1", "yes"}


def _as_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _as_list(value: Any) -> list[str]:
    """Parse an ARRAY<STRING> column, which arrives as a JSON string."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a plain comma-separated list authored by hand.
        return [p.strip() for p in text.split(",") if p.strip()]
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    return [str(parsed)]


def _parameter_from_row(row: dict[str, Any]) -> QueryParameter:
    raw_type = str(row.get("param_type") or "").strip().upper()
    try:
        param_type = ParamType(raw_type)
    except ValueError as exc:
        raise ValidationError(
            technical=(
                f"query_parameter.param_type {raw_type!r} for "
                f"{row.get('query_id')}.{row.get('param_name')} is not a supported type"
            )
        ) from exc

    return QueryParameter(
        query_id=str(row.get("query_id")),
        param_name=str(row.get("param_name")),
        label=str(row.get("label") or row.get("param_name")),
        param_type=param_type,
        sql_type=row.get("sql_type"),
        help_text=row.get("help_text"),
        is_required=_as_bool(row.get("is_required")),
        default_value=row.get("default_value"),
        options_sql=row.get("options_sql"),
        options_static=_as_list(row.get("options_static")),
        max_range_days=_as_int(row.get("max_range_days")),
        display_order=_as_int(row.get("display_order")) or 0,
    )


def _query_from_row(row: dict[str, Any]) -> QueryDefinition:
    return QueryDefinition(
        query_id=str(row.get("query_id")),
        title=str(row.get("title") or row.get("query_id")),
        sql_template=str(row.get("sql_template") or ""),
        description=row.get("description"),
        category=row.get("category"),
        allowed_groups=_as_list(row.get("allowed_groups")),
        warehouse_id=row.get("warehouse_id"),
        max_rows=_as_int(row.get("max_rows")),
        timeout_seconds=_as_int(row.get("timeout_seconds")),
        owner_email=row.get("owner_email"),
    )


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #


class MetadataRepository:
    """Reads the metadata tables with the *service principal* client."""

    def __init__(self, client: WorkspaceClient, settings: Settings):
        self._client = client
        self._settings = settings

    def _run(self, statement: str, parameters: Sequence[StatementParameterListItem] | None = None):
        return self._client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self._settings.warehouse_id,
            parameters=list(parameters) if parameters else None,
            wait_timeout="30s",
        )

    def list_queries(self) -> list[QueryDefinition]:
        """All active queries, without their parameters (cheap enough for a list view)."""
        response = self._run(sql.select_active_queries(self._settings))
        return [_query_from_row(row) for row in rows_as_dicts(response)]

    def get_query(self, query_id: str) -> QueryDefinition:
        """One active query with its parameters attached, ordered for rendering."""
        response = self._run(
            sql.select_query_by_id(self._settings),
            [StatementParameterListItem(name="query_id", value=query_id, type="STRING")],
        )
        rows = rows_as_dicts(response)
        if not rows:
            raise PortalError(
                "Consulta não encontrada ou desativada.",
                technical=f"no active query_catalog row for query_id={query_id!r}",
            )

        definition = _query_from_row(rows[0])
        parameters = self.list_parameters(query_id)
        return QueryDefinition(**{**definition.__dict__, "parameters": parameters})

    def list_parameters(self, query_id: str) -> list[QueryParameter]:
        response = self._run(
            sql.select_query_parameters(self._settings),
            [StatementParameterListItem(name="query_id", value=query_id, type="STRING")],
        )
        return [_parameter_from_row(row) for row in rows_as_dicts(response)]


def visible_queries(
    queries: Sequence[QueryDefinition],
    user_groups: set[str],
) -> list[QueryDefinition]:
    """Filter the catalog to what this user should *see*.

    This is presentation only. A user who reaches a query by other means is still
    stopped by Unity Catalog grants on the underlying tables — that is the actual
    authorization boundary. See README, "allowed_groups is not security".
    """
    normalized = {g.strip().lower() for g in user_groups if g and g.strip()}
    return [
        q
        for q in queries
        if q.is_public or any(g.strip().lower() in normalized for g in q.allowed_groups)
    ]
