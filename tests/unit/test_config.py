"""Warehouse-id resolution and the diagnostics that make a bad binding obvious."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from portal.config import (
    WAREHOUSE_ENV_VARS,
    ConfigError,
    discover_warehouse,
    load_settings,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from an environment with no portal configuration."""
    for name in (*WAREHOUSE_ENV_VARS, "PORTAL_CATALOG", "PORTAL_SCHEMA",
                 "PORTAL_TIMEZONE", "PORTAL_GROUP_FILTERING", "PORTAL_HISTORY_LIMIT"):
        monkeypatch.delenv(name, raising=False)


@dataclass
class FakeWarehouse:
    id: str
    name: str = "wh"


class FakeWarehouses:
    def __init__(self, items):
        self._items = items

    def list(self):
        return list(self._items)


class FakeClient:
    def __init__(self, items):
        self.warehouses = FakeWarehouses(items)


# --------------------------------------------------------------------------- #
# Resolution order
# --------------------------------------------------------------------------- #


def test_explicit_argument_wins(monkeypatch):
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "from-env")
    assert load_settings("explicit").warehouse_id == "explicit"


@pytest.mark.parametrize("name", WAREHOUSE_ENV_VARS)
def test_any_supported_env_var_is_accepted(monkeypatch, name):
    """The resource key differs between UI and bundle deploys, so several spellings work."""
    monkeypatch.setenv(name, "wh-123")
    assert load_settings().warehouse_id == "wh-123"


def test_first_listed_variable_takes_precedence(monkeypatch):
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "primary")
    monkeypatch.setenv("WAREHOUSE_ID", "secondary")
    assert load_settings().warehouse_id == "primary"


def test_blank_value_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "   ")
    with pytest.raises(ConfigError):
        load_settings()


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #


def test_error_names_every_variable_it_checked():
    with pytest.raises(ConfigError) as exc:
        load_settings()

    message = str(exc.value)
    for name in WAREHOUSE_ENV_VARS:
        assert name in message


def test_error_explains_the_valueFrom_resource_key_trap():
    """The actual root cause of the first deploy failure: valueFrom needs a resource key."""
    with pytest.raises(ConfigError) as exc:
        load_settings()

    message = str(exc.value)
    assert "valueFrom" in message
    assert "sql-warehouse" in message


def test_error_lists_present_variables_without_leaking_tokens(monkeypatch):
    monkeypatch.setenv("PORTAL_CATALOG", "workspace")
    monkeypatch.setenv("DATABRICKS_TOKEN", "super-secret-value")

    with pytest.raises(ConfigError) as exc:
        load_settings()

    message = str(exc.value)
    assert "PORTAL_CATALOG" in message
    assert "super-secret-value" not in message
    assert "DATABRICKS_TOKEN" not in message


# --------------------------------------------------------------------------- #
# Single-warehouse fallback
# --------------------------------------------------------------------------- #


def test_discovery_uses_the_only_visible_warehouse():
    assert discover_warehouse(FakeClient([FakeWarehouse("wh-only")])) == "wh-only"


def test_discovery_refuses_to_guess_between_several():
    client = FakeClient([FakeWarehouse("wh-a"), FakeWarehouse("wh-b")])
    assert discover_warehouse(client) is None


def test_discovery_handles_no_warehouses():
    assert discover_warehouse(FakeClient([])) is None


def test_discovery_survives_an_api_failure():
    class Exploding:
        def list(self):
            raise RuntimeError("permission denied")

    class Client:
        warehouses = Exploding()

    assert discover_warehouse(Client()) is None


def test_discovery_is_off_unless_requested():
    """The CLI must fail loudly rather than silently pick a warehouse."""
    with pytest.raises(ConfigError):
        load_settings(discover=False)


# --------------------------------------------------------------------------- #
# Free Edition defaults
# --------------------------------------------------------------------------- #


def test_catalog_and_schema_default_to_free_edition(monkeypatch):
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")
    settings = load_settings()
    assert (settings.catalog, settings.schema) == ("workspace", "portal")


def test_group_filtering_can_be_disabled(monkeypatch):
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")
    monkeypatch.setenv("PORTAL_GROUP_FILTERING", "false")
    assert load_settings().group_filtering is False


def test_invalid_timezone_is_rejected(monkeypatch):
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-1")
    monkeypatch.setenv("PORTAL_TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(ConfigError):
        load_settings()
