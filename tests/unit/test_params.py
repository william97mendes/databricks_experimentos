"""Parameter typing: every user value leaves as a typed StatementParameterListItem."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from portal.errors import ValidationError
from portal.params import (
    RANGE_END_SUFFIX,
    RANGE_START_SUFFIX,
    ParamType,
    bind,
    to_statement_parameters,
)

from tests.conftest import make_param


def by_name(items):
    return {item.name: item for item in items}


def test_date_range_splits_into_two_typed_markers():
    param = make_param("periodo", ParamType.DATE_RANGE, sql_type="DATE")

    items = by_name(
        to_statement_parameters([param], {"periodo": (date(2026, 2, 1), date(2026, 2, 28))})
    )

    assert set(items) == {"periodo" + RANGE_START_SUFFIX, "periodo" + RANGE_END_SUFFIX}
    assert items["periodo_inicio"].value == "2026-02-01"
    assert items["periodo_fim"].value == "2026-02-28"
    assert {i.type for i in items.values()} == {"DATE"}


def test_each_param_type_gets_an_explicit_sql_type():
    params = [
        make_param("d", ParamType.DATE),
        make_param("s", ParamType.STRING),
        make_param("n", ParamType.INT),
        make_param("v", ParamType.DECIMAL),
        make_param("sel", ParamType.SELECT),
        make_param("multi", ParamType.MULTI_SELECT),
    ]
    values = {
        "d": date(2026, 3, 1),
        "s": "texto",
        "n": "7",
        "v": "10,50",
        "sel": "A",
        "multi": ["Sul", "Sudeste"],
    }

    items = by_name(to_statement_parameters(params, values))

    assert all(item.type for item in items.values()), "no parameter may be untyped"
    assert items["d"].type == "DATE" and items["d"].value == "2026-03-01"
    assert items["n"].type == "INT" and items["n"].value == "7"
    assert items["v"].type.startswith("DECIMAL") and Decimal(items["v"].value) == Decimal("10.50")
    assert items["s"].type == "STRING"


def test_explicit_sql_type_overrides_the_default():
    param = make_param("v", ParamType.DECIMAL, sql_type="DECIMAL(18,2)")
    assert to_statement_parameters([param], {"v": "1.5"})[0].type == "DECIMAL(18,2)"


def test_multi_select_binds_one_comma_joined_string():
    """The API has no ARRAY type, so templates unpack with split(:p, ',')."""
    param = make_param("regioes", ParamType.MULTI_SELECT)

    item = to_statement_parameters([param], {"regioes": ["Sul", "Sudeste", "Norte"]})[0]

    assert item.type == "STRING"
    assert item.value == "Sul,Sudeste,Norte"


def test_missing_required_parameter_is_rejected_in_portuguese():
    param = make_param("periodo", ParamType.DATE_RANGE, is_required=True, label="Período")

    with pytest.raises(ValidationError) as exc:
        to_statement_parameters([param], {})

    assert "Período" in exc.value.user_message
    assert "obrigatório" in exc.value.user_message


def test_optional_blank_parameter_binds_null_so_markers_still_resolve():
    param = make_param("regioes", ParamType.MULTI_SELECT, is_required=False)

    items = to_statement_parameters([param], {"regioes": []})

    assert len(items) == 1
    assert items[0].value is None


def test_optional_blank_date_range_binds_both_markers_as_null():
    param = make_param("periodo", ParamType.DATE_RANGE, is_required=False)

    items = by_name(to_statement_parameters([param], {"periodo": None}))

    assert set(items) == {"periodo_inicio", "periodo_fim"}
    assert all(i.value is None for i in items.values())


@pytest.mark.parametrize(
    ("param_type", "value"),
    [(ParamType.INT, "abc"), (ParamType.DECIMAL, "não é número")],
)
def test_bad_numeric_input_is_rejected_before_submission(param_type, value):
    param = make_param("campo", param_type, label="Valor")

    with pytest.raises(ValidationError) as exc:
        to_statement_parameters([param], {"campo": value})

    assert "Valor" in exc.value.user_message


def test_decimal_accepts_brazilian_comma_decimal_separator():
    param = make_param("v", ParamType.DECIMAL)
    item = to_statement_parameters([param], {"v": "1234,56"})[0]
    assert Decimal(item.value) == Decimal("1234.56")


def test_injection_attempt_stays_a_value_and_never_becomes_sql():
    param = make_param("nome", ParamType.STRING)
    payload = "'; DROP TABLE governance.portal.query_catalog; --"

    item = to_statement_parameters([param], {"nome": payload})[0]

    # Carried verbatim as a typed parameter value: the server treats it as data.
    assert item.value == payload
    assert item.type == "STRING"


def test_bind_returns_audit_view_matching_the_bound_items():
    params = [
        make_param("periodo", ParamType.DATE_RANGE),
        make_param("n", ParamType.INT),
    ]

    bound = bind(params, {"periodo": (date(2026, 1, 1), date(2026, 1, 31)), "n": 5})

    assert bound.recorded == {
        "periodo_inicio": "2026-01-01",
        "periodo_fim": "2026-01-31",
        "n": "5",
    }
    assert len(bound.items) == 3
