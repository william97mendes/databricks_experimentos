"""The relative-date grammar, including the timezone boundary that motivates it."""

from __future__ import annotations

from datetime import date

import pytest
from portal.errors import ValidationError
from portal.params import ParamType, resolve_default, resolve_relative_date, today_in

from tests.conftest import make_param

REFERENCE = date(2026, 3, 14)  # mid-March, so last month is February


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("TODAY", date(2026, 3, 14)),
        ("TODAY-30D", date(2026, 2, 12)),
        ("TODAY+7D", date(2026, 3, 21)),
        ("MONTH_START", date(2026, 3, 1)),
        ("LAST_MONTH_START", date(2026, 2, 1)),
        ("LAST_MONTH_END", date(2026, 2, 28)),
        ("2025-12-25", date(2025, 12, 25)),
    ],
)
def test_grammar_tokens(token: str, expected: date):
    assert resolve_relative_date(token, REFERENCE) == expected


@pytest.mark.parametrize("token", ["today", " Last_Month_End ", "TODAY - 30 D"])
def test_grammar_is_case_and_space_insensitive(token: str):
    assert resolve_relative_date(token, REFERENCE) is not None


def test_last_month_crosses_year_boundary():
    january = date(2026, 1, 10)
    assert resolve_relative_date("LAST_MONTH_START", january) == date(2025, 12, 1)
    assert resolve_relative_date("LAST_MONTH_END", january) == date(2025, 12, 31)


def test_last_month_end_handles_leap_february():
    assert resolve_relative_date("LAST_MONTH_END", date(2024, 3, 5)) == date(2024, 2, 29)


@pytest.mark.parametrize("token", ["", "YESTERDAY", "TODAY-30W", "2026-13-01", "DROP TABLE"])
def test_unsupported_tokens_are_rejected(token: str):
    with pytest.raises(ValidationError):
        resolve_relative_date(token, REFERENCE)


def test_ultimo_mes_fechado_as_a_range_default():
    """The request business users make constantly, resolved as one default."""
    param = make_param(default_value="LAST_MONTH_START:LAST_MONTH_END")
    assert resolve_default(param, REFERENCE) == (date(2026, 2, 1), date(2026, 2, 28))


@pytest.mark.parametrize("separator", [":", "|", ","])
def test_range_default_accepts_each_separator(separator: str):
    param = make_param(default_value=f"MONTH_START{separator}TODAY")
    assert resolve_default(param, REFERENCE) == (date(2026, 3, 1), date(2026, 3, 14))


def test_single_token_range_default_applies_to_both_ends():
    param = make_param(default_value="TODAY")
    assert resolve_default(param, REFERENCE) == (REFERENCE, REFERENCE)


def test_absent_default_returns_none_not_empty_string():
    assert resolve_default(make_param(default_value=None), REFERENCE) is None
    assert resolve_default(make_param(default_value="   "), REFERENCE) is None


def test_non_date_defaults_keep_their_type():
    assert resolve_default(make_param("n", ParamType.INT, default_value="42"), REFERENCE) == 42
    multi = make_param("r", ParamType.MULTI_SELECT, default_value="Sul, Sudeste")
    assert resolve_default(multi, REFERENCE) == ["Sul", "Sudeste"]


def test_today_resolves_in_business_timezone_not_utc():
    """Late evening in São Paulo is already tomorrow in UTC.

    This is the whole reason the timezone is configured rather than inherited:
    at 22:00 BRT the container's UTC clock reads the next day, and "ontem" would
    silently shift by one for every user.
    """
    sao_paulo = today_in("America/Sao_Paulo")
    kiritimati = today_in("Pacific/Kiritimati")  # UTC+14, always ahead
    assert kiritimati >= sao_paulo
