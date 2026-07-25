"""Parameter typing, relative-date defaults, and validation.

Every user value leaves this module as a `StatementParameterListItem` with an
explicit SQL type. Nothing here returns SQL text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from databricks.sdk.service.sql import StatementParameterListItem

from portal.errors import ValidationError

if TYPE_CHECKING:  # avoids a metadata <-> params import cycle
    from portal.metadata import QueryParameter

# DATE_RANGE binds two markers; templates reference :<name>_inicio and :<name>_fim.
RANGE_START_SUFFIX = "_inicio"
RANGE_END_SUFFIX = "_fim"

# MULTI_SELECT binds a single comma-joined STRING. Templates must unpack it with
# split(:param, ','), because the Statement Execution API has no ARRAY parameter type.
MULTI_SELECT_SEPARATOR = ","


class ParamType(str, Enum):  # noqa: UP042 - keep str-mixin semantics for value comparisons
    DATE = "DATE"
    DATE_RANGE = "DATE_RANGE"
    STRING = "STRING"
    INT = "INT"
    DECIMAL = "DECIMAL"
    SELECT = "SELECT"
    MULTI_SELECT = "MULTI_SELECT"


_DEFAULT_SQL_TYPE = {
    ParamType.DATE: "DATE",
    ParamType.DATE_RANGE: "DATE",
    ParamType.STRING: "STRING",
    ParamType.INT: "INT",
    ParamType.DECIMAL: "DECIMAL(38,10)",
    ParamType.SELECT: "STRING",
    ParamType.MULTI_SELECT: "STRING",
}

_DATE_TYPES = {ParamType.DATE, ParamType.DATE_RANGE}


# --------------------------------------------------------------------------- #
# Relative-date grammar
# --------------------------------------------------------------------------- #

_TODAY_OFFSET_RE = re.compile(r"^TODAY\s*([+-])\s*(\d+)\s*D$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Accepts "LAST_MONTH_START:LAST_MONTH_END", "...|...", or "...,..." for DATE_RANGE.
_RANGE_SPLIT_RE = re.compile(r"\s*[:|,]\s*")


def today_in(timezone: str | ZoneInfo) -> date:
    """Current date in the business timezone, not the container's UTC."""
    tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    return datetime.now(tz).date()


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _last_month_end(day: date) -> date:
    return _month_start(day) - _days(1)


def _last_month_start(day: date) -> date:
    return _month_start(_last_month_end(day))


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def resolve_relative_date(token: str, today: date) -> date:
    """Resolve one token of the relative-date grammar against `today`.

    Supported: TODAY, TODAY-<n>D, TODAY+<n>D, MONTH_START, LAST_MONTH_START,
    LAST_MONTH_END, and literal ISO dates (YYYY-MM-DD).

    LAST_MONTH_START/LAST_MONTH_END together express "último mês fechado", which
    is what business users ask for constantly.
    """
    raw = (token or "").strip()
    if not raw:
        raise ValidationError(technical="Empty relative-date token")

    if _ISO_DATE_RE.match(raw):
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(
                f"Data inválida: {raw}", technical=f"bad ISO date {raw!r}"
            ) from exc

    upper = raw.upper()
    if upper == "TODAY":
        return today
    if upper == "MONTH_START":
        return _month_start(today)
    if upper == "LAST_MONTH_START":
        return _last_month_start(today)
    if upper == "LAST_MONTH_END":
        return _last_month_end(today)

    match = _TODAY_OFFSET_RE.match(upper)
    if match:
        sign, amount = match.group(1), int(match.group(2))
        return today + _days(amount if sign == "+" else -amount)

    raise ValidationError(
        f"Valor padrão inválido: {raw}", technical=f"unsupported relative-date token {raw!r}"
    )


def resolve_default(param: QueryParameter, today: date) -> Any:
    """Resolve `default_value` into a widget-ready Python value.

    Returns `None` when no default is configured, so callers can distinguish
    "no default" from "default is empty string".
    """
    raw = (param.default_value or "").strip()
    if not raw:
        return None

    ptype = param.param_type
    if ptype is ParamType.DATE:
        return resolve_relative_date(raw, today)
    if ptype is ParamType.DATE_RANGE:
        parts = [p for p in _RANGE_SPLIT_RE.split(raw) if p]
        if len(parts) == 1:
            parts = parts * 2
        if len(parts) != 2:
            raise ValidationError(
                f"Período padrão inválido para {param.label}",
                technical=f"DATE_RANGE default {raw!r} must hold one or two tokens",
            )
        return (
            resolve_relative_date(parts[0], today),
            resolve_relative_date(parts[1], today),
        )
    if ptype is ParamType.INT:
        return _coerce_int(param, raw)
    if ptype is ParamType.DECIMAL:
        return _coerce_decimal(param, raw)
    if ptype is ParamType.MULTI_SELECT:
        return [p.strip() for p in raw.split(MULTI_SELECT_SEPARATOR) if p.strip()]
    return raw


# --------------------------------------------------------------------------- #
# Coercion and validation
# --------------------------------------------------------------------------- #


def _coerce_int(param: QueryParameter, value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"O campo {param.label} deve ser um número inteiro.",
            technical=f"{param.param_name}={value!r} is not an int",
        ) from exc


def _coerce_decimal(param: QueryParameter, value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            f"O campo {param.label} deve ser um número.",
            technical=f"{param.param_name}={value!r} is not a decimal",
        ) from exc


def _coerce_date(param: QueryParameter, value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return resolve_relative_date(str(value), today=date.today())


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    return False


def validate_range(param: QueryParameter, start: date, end: date) -> None:
    """Check ordering and `max_range_days` before submission, never after.

    A user who waits 40s only to be told the period is too wide has been failed
    twice, so this runs client-side ahead of the API call.
    """
    if start > end:
        raise ValidationError(
            f"No campo {param.label}, a data inicial não pode ser maior que a final.",
            technical=f"{param.param_name}: start {start} > end {end}",
        )
    limit = param.max_range_days
    if limit is None or limit <= 0:
        return
    span = (end - start).days + 1  # inclusive, as business users count days
    if span > limit:
        raise ValidationError(
            f"O período de {param.label} não pode exceder {limit} dias. "
            f"Você selecionou {span} dias, reduza o intervalo.",
            technical=f"{param.param_name}: span {span} > max_range_days {limit}",
        )


def _sql_type(param: QueryParameter) -> str:
    configured = (param.sql_type or "").strip()
    return configured or _DEFAULT_SQL_TYPE[param.param_type]


def _item(name: str, value: str, sql_type: str) -> StatementParameterListItem:
    return StatementParameterListItem(name=name, value=value, type=sql_type)


def to_statement_parameters(
    parameters: Sequence[QueryParameter],
    values: dict[str, Any],
) -> list[StatementParameterListItem]:
    """Convert raw widget/CLI values into typed statement parameters.

    Raises `ValidationError` with Portuguese copy for missing required inputs,
    bad types, and out-of-bounds ranges. DATE_RANGE expands into two markers.
    """
    items: list[StatementParameterListItem] = []

    for param in parameters:
        raw = values.get(param.param_name)

        if _is_blank(raw):
            if param.is_required:
                raise ValidationError(
                    f"O campo {param.label} é obrigatório.",
                    technical=f"missing required parameter {param.param_name}",
                )
            items.extend(_null_items(param))
            continue

        items.extend(_typed_items(param, raw))

    return items


def _null_items(param: QueryParameter) -> Iterable[StatementParameterListItem]:
    """Bind explicit NULLs so the template's markers always resolve."""
    sql_type = _sql_type(param)
    if param.param_type is ParamType.DATE_RANGE:
        return [
            _item(param.param_name + RANGE_START_SUFFIX, None, sql_type),
            _item(param.param_name + RANGE_END_SUFFIX, None, sql_type),
        ]
    return [_item(param.param_name, None, sql_type)]


def _typed_items(param: QueryParameter, raw: Any) -> list[StatementParameterListItem]:
    sql_type = _sql_type(param)
    ptype = param.param_type

    if ptype is ParamType.DATE_RANGE:
        start, end = _unpack_range(param, raw)
        validate_range(param, start, end)
        return [
            _item(param.param_name + RANGE_START_SUFFIX, start.isoformat(), sql_type),
            _item(param.param_name + RANGE_END_SUFFIX, end.isoformat(), sql_type),
        ]

    if ptype is ParamType.DATE:
        return [_item(param.param_name, _coerce_date(param, raw).isoformat(), sql_type)]

    if ptype is ParamType.INT:
        return [_item(param.param_name, str(_coerce_int(param, raw)), sql_type)]

    if ptype is ParamType.DECIMAL:
        return [_item(param.param_name, str(_coerce_decimal(param, raw)), sql_type)]

    if ptype is ParamType.MULTI_SELECT:
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        joined = MULTI_SELECT_SEPARATOR.join(str(v).strip() for v in values)
        return [_item(param.param_name, joined, sql_type)]

    return [_item(param.param_name, str(raw), sql_type)]


def _unpack_range(param: QueryParameter, raw: Any) -> tuple[date, date]:
    """Accept st.date_input's tuple, or a two-item sequence/string."""
    if isinstance(raw, (list, tuple)):
        if len(raw) != 2:
            raise ValidationError(
                f"Selecione a data inicial e a data final em {param.label}.",
                technical=f"{param.param_name}: expected 2 dates, got {len(raw)}",
            )
        return _coerce_date(param, raw[0]), _coerce_date(param, raw[1])

    parts = [p for p in _RANGE_SPLIT_RE.split(str(raw)) if p]
    if len(parts) != 2:
        raise ValidationError(
            f"Selecione a data inicial e a data final em {param.label}.",
            technical=f"{param.param_name}: cannot unpack range from {raw!r}",
        )
    return _coerce_date(param, parts[0]), _coerce_date(param, parts[1])


@dataclass(frozen=True)
class BoundParameters:
    """Typed parameters plus the plain dict recorded in the audit log."""

    items: list[StatementParameterListItem]
    recorded: dict[str, Any]


def bind(
    parameters: Sequence[QueryParameter],
    values: dict[str, Any],
) -> BoundParameters:
    """Validate and type all inputs, returning both the API items and audit view."""
    items = to_statement_parameters(parameters, values)
    recorded = {item.name: item.value for item in items}
    return BoundParameters(items=items, recorded=recorded)
