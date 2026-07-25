"""max_range_days is enforced before submission, with a message that says what to do."""

from __future__ import annotations

from datetime import date

import pytest
from portal.errors import ValidationError
from portal.params import ParamType, to_statement_parameters, validate_range

from tests.conftest import make_param


def test_range_within_limit_passes():
    param = make_param(max_range_days=31)
    validate_range(param, date(2026, 3, 1), date(2026, 3, 31))


def test_span_is_inclusive_of_both_endpoints():
    """31 days selected means 31, the way a business user counts them."""
    param = make_param(max_range_days=31)
    validate_range(param, date(2026, 3, 1), date(2026, 3, 31))

    with pytest.raises(ValidationError):
        validate_range(param, date(2026, 3, 1), date(2026, 4, 1))


def test_exceeding_the_limit_names_the_limit_and_the_selection():
    param = make_param(label="Período", max_range_days=31)

    with pytest.raises(ValidationError) as exc:
        validate_range(param, date(2026, 1, 1), date(2026, 6, 30))

    message = exc.value.user_message
    assert "Período" in message
    assert "31" in message and "181" in message
    assert "reduza o intervalo" in message.lower()


def test_inverted_range_is_rejected():
    param = make_param(label="Período")

    with pytest.raises(ValidationError) as exc:
        validate_range(param, date(2026, 3, 31), date(2026, 3, 1))

    assert "não pode ser maior" in exc.value.user_message


@pytest.mark.parametrize("limit", [None, 0])
def test_absent_limit_allows_any_span(limit):
    param = make_param(max_range_days=limit)
    validate_range(param, date(2020, 1, 1), date(2026, 12, 31))


def test_validation_runs_during_binding_not_after_execution():
    """The user must be told before the wait, not after it."""
    param = make_param("periodo", ParamType.DATE_RANGE, label="Período", max_range_days=7)

    with pytest.raises(ValidationError):
        to_statement_parameters([param], {"periodo": (date(2026, 1, 1), date(2026, 12, 31))})
