"""The only module permitted to build SQL text by substitution.

Everything else in `portal` receives finished statements from here and passes user
values exclusively as `StatementParameterListItem`. `tests/unit/test_no_sql_interpolation.py`
enforces that rule statically and exempts this module, which is why this file is
deliberately small enough to review by eye.

Two invariants hold here:

1. Only *identifiers* are ever substituted, and only after passing `identifier()`,
   which admits nothing but `[A-Za-z_][A-Za-z0-9_]*`.
2. No caller-supplied *value* is substituted. Values always travel as named
   parameter markers (`:name`).
"""

from __future__ import annotations

import re

from portal.config import Settings

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

QUERY_CATALOG_TABLE = "query_catalog"
QUERY_PARAMETER_TABLE = "query_parameter"
EXECUTION_LOG_TABLE = "execution_log"


class UnsafeIdentifierError(ValueError):
    """Raised when a catalog/schema/table name is not a bare SQL identifier."""


def identifier(value: str) -> str:
    """Return `value` if it is a bare SQL identifier, else raise.

    This is the chokepoint that makes substitution below safe: a catalog name of
    `main; DROP TABLE x` never reaches a statement.
    """
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise UnsafeIdentifierError(f"{value!r} is not a valid SQL identifier")
    return value


def qualify(settings: Settings, table: str) -> str:
    """Fully qualified `catalog.schema.table`, every part validated."""
    return f"{identifier(settings.catalog)}.{identifier(settings.schema)}.{identifier(table)}"


_QUERY_CATALOG_SELECT = """
SELECT query_id, title, description, category, sql_template, allowed_groups,
       warehouse_id, max_rows, timeout_seconds, owner_email
FROM {table}
WHERE is_active = true
ORDER BY category, title
"""

_QUERY_CATALOG_BY_ID = """
SELECT query_id, title, description, category, sql_template, allowed_groups,
       warehouse_id, max_rows, timeout_seconds, owner_email
FROM {table}
WHERE query_id = :query_id AND is_active = true
"""

_QUERY_PARAMETER_SELECT = """
SELECT query_id, param_name, label, help_text, param_type, sql_type, is_required,
       default_value, options_sql, options_static, max_range_days, display_order
FROM {table}
WHERE query_id = :query_id
ORDER BY display_order, param_name
"""


def select_active_queries(settings: Settings) -> str:
    return _QUERY_CATALOG_SELECT.format(table=qualify(settings, QUERY_CATALOG_TABLE))


def select_query_by_id(settings: Settings) -> str:
    return _QUERY_CATALOG_BY_ID.format(table=qualify(settings, QUERY_CATALOG_TABLE))


def select_query_parameters(settings: Settings) -> str:
    return _QUERY_PARAMETER_SELECT.format(table=qualify(settings, QUERY_PARAMETER_TABLE))


def group_membership(count: int) -> str:
    """Statement testing membership in `count` groups in a single round trip.

    Only the *number* of groups shapes the text; the group names themselves bind
    as `:g0`..`:gN`. Runs with the user's token, so it reports the caller's own
    membership as Unity Catalog sees it.
    """
    if count < 1:
        raise ValueError("group_membership requires at least one group")
    branches = [
        f"SELECT :g{i} AS group_name, is_account_group_member(:g{i}) AS is_member"
        for i in range(count)
    ]
    return "\nUNION ALL\n".join(branches)
