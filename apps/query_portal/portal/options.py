"""Dropdown options for SELECT / MULTI_SELECT parameters.

`options_sql` runs with the **user's** client, never the service principal: a
dropdown that lists values the user could not have queried themselves is a data
leak, even when they never select one.

The statement text comes from the metadata table (authored by the data team), so
it is trusted SQL — but it still carries no user input, because option queries
take no parameters.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient

from portal.config import Settings
from portal.metadata import QueryParameter, rows_as_dicts


def load_options(
    user_client: WorkspaceClient,
    settings: Settings,
    param: QueryParameter,
) -> list[str]:
    """Return the selectable values for `param`.

    Static options win when both are configured, because they cost no round trip.
    An option query that fails yields an empty list rather than breaking the form;
    the widget then renders empty and the user can still run other queries.
    """
    if param.options_static:
        return list(param.options_static)

    statement = (param.options_sql or "").strip()
    if not statement:
        return []

    try:
        response = user_client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=settings.warehouse_id,
            wait_timeout="30s",
        )
    except Exception as exc:  # noqa: BLE001 - a broken dropdown must not break the form
        print(f"[options] {param.query_id}.{param.param_name} failed: {exc}")
        return []

    # Take the first column of each row, ignoring any label columns the author
    # added for readability.
    values: list[str] = []
    for row in rows_as_dicts(response):
        for value in row.values():
            if value is not None:
                values.append(str(value))
            break
    return values
