"""Runtime settings, resolved from environment variables.

The bundle injects these through `app.yaml` env entries; the phase-1 CLI reads the
same variables so both paths are configured identically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

# Serverless containers run in UTC. Relative-date defaults ("último mês fechado")
# must resolve in the business timezone or they are wrong for ~3h every day.
DEFAULT_TIMEZONE = "America/Sao_Paulo"


class ConfigError(RuntimeError):
    """Raised when required configuration is absent."""


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

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Environment variable {name} is not set. "
            "In the app it comes from app.yaml; locally, export it or use --warehouse-id."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def load_settings(warehouse_id: str | None = None) -> Settings:
    """Build settings from the environment.

    `warehouse_id` overrides `DATABRICKS_WAREHOUSE_ID`, so the CLI can target a
    warehouse without mutating the environment.
    """
    resolved_warehouse = (warehouse_id or "").strip() or _required("DATABRICKS_WAREHOUSE_ID")
    timezone = os.environ.get("PORTAL_TIMEZONE", "").strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except Exception as exc:  # noqa: BLE001 - surfaced as configuration error
        raise ConfigError(f"PORTAL_TIMEZONE {timezone!r} is not a valid IANA timezone") from exc

    return Settings(
        catalog=_required("PORTAL_CATALOG"),
        schema=_required("PORTAL_SCHEMA"),
        warehouse_id=resolved_warehouse,
        timezone=timezone,
        external_links_threshold=_int("PORTAL_EXTERNAL_LINKS_THRESHOLD", 50_000),
        default_max_rows=_int("PORTAL_DEFAULT_MAX_ROWS", 100_000),
        default_timeout_seconds=_int("PORTAL_DEFAULT_TIMEOUT_SECONDS", 300),
    )
