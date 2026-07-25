"""Runtime settings, resolved from environment variables.

The bundle injects these through `app.yaml` env entries; the CLI reads the same
variables, so both paths are configured identically.

Defaults target Databricks Free Edition (catalog `workspace`, schema `portal`),
which keeps first-run setup to a single required variable: the warehouse id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

# Serverless containers run in UTC. Relative-date defaults ("último mês fechado")
# must resolve in the business timezone or they are wrong for ~3h every day.
DEFAULT_TIMEZONE = "America/Sao_Paulo"

# Free Edition ships a `workspace` catalog and has no account console, so this is
# the catalog a new user actually has. Override per bundle target.
DEFAULT_CATALOG = "workspace"
DEFAULT_SCHEMA = "portal"


class ConfigError(RuntimeError):
    """Raised when required configuration is absent or invalid."""


@dataclass(frozen=True)
class Settings:
    catalog: str
    schema: str
    warehouse_id: str
    timezone: str = DEFAULT_TIMEZONE
    # Above this row count, results are fetched as EXTERNAL_LINKS and streamed
    # chunk by chunk into the download instead of being held in memory.
    external_links_threshold: int = 50_000
    default_max_rows: int = 100_000
    default_timeout_seconds: int = 300
    poll_interval_seconds: float = 1.0
    # Group filtering needs account groups, which Free Edition cannot create
    # (no account console, no SCIM). Turning it off shows every query in the
    # list and is SAFE: allowed_groups was never the security boundary — Unity
    # Catalog grants are, and they still apply. See README.
    group_filtering: bool = True
    # Rows shown in "Minhas execuções".
    history_limit: int = 20

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Environment variable {name} is not set. "
            "In the app it comes from app.yaml; locally, export it or pass --warehouse-id."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Environment variable {name} must be a boolean, got {raw!r}")


def load_settings(warehouse_id: str | None = None) -> Settings:
    """Build settings from the environment.

    `warehouse_id` overrides `DATABRICKS_WAREHOUSE_ID` so the CLI can target a
    warehouse without mutating the environment.
    """
    resolved_warehouse = (warehouse_id or "").strip() or _required("DATABRICKS_WAREHOUSE_ID")
    timezone = _optional("PORTAL_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        ZoneInfo(timezone)
    except Exception as exc:  # noqa: BLE001 - surfaced as a configuration error
        raise ConfigError(f"PORTAL_TIMEZONE {timezone!r} is not a valid IANA timezone") from exc

    return Settings(
        catalog=_optional("PORTAL_CATALOG", DEFAULT_CATALOG),
        schema=_optional("PORTAL_SCHEMA", DEFAULT_SCHEMA),
        warehouse_id=resolved_warehouse,
        timezone=timezone,
        external_links_threshold=_int("PORTAL_EXTERNAL_LINKS_THRESHOLD", 50_000),
        default_max_rows=_int("PORTAL_DEFAULT_MAX_ROWS", 100_000),
        default_timeout_seconds=_int("PORTAL_DEFAULT_TIMEOUT_SECONDS", 300),
        group_filtering=_bool("PORTAL_GROUP_FILTERING", True),
        history_limit=_int("PORTAL_HISTORY_LIMIT", 20),
    )
