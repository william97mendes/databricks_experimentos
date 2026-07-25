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


# The warehouse id may arrive under any of these names. `valueFrom` in app.yaml
# binds an app *resource* to an env var, and the resource key differs between a
# bundle deploy and an app created by hand in the UI, so several spellings are
# accepted rather than making one of them load-bearing.
WAREHOUSE_ENV_VARS = (
    "DATABRICKS_WAREHOUSE_ID",
    "DATABRICKS_SQL_WAREHOUSE_ID",
    "SQL_WAREHOUSE_ID",
    "WAREHOUSE_ID",
)


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


def _warehouse_from_env() -> str | None:
    for name in WAREHOUSE_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _warehouse_diagnostic() -> str:
    """Explain what was checked and what the runtime actually provided.

    Listing the names present (never their values) turns "not set" from a dead
    end into something the operator can act on in one glance.
    """
    checked = ", ".join(WAREHOUSE_ENV_VARS)
    visible = sorted(
        name
        for name in os.environ
        if name.startswith(("PORTAL_", "DATABRICKS_")) and "TOKEN" not in name
    )
    present = ", ".join(visible) if visible else "(nenhuma)"
    return (
        "Nenhum ID de SQL warehouse foi encontrado.\n\n"
        f"Variáveis procuradas: {checked}\n"
        f"Variáveis presentes no ambiente: {present}\n\n"
        "No app, o ID vem do app.yaml. Se você usa 'valueFrom', o valor precisa ser a "
        "CHAVE de um recurso declarado no app (o padrão é 'sql-warehouse'), e nao um "
        "nome livre. Confira em Compute > Apps > seu app > Resources, ou troque por um "
        "'value:' com o ID literal.\n"
        "Fora do app, exporte DATABRICKS_WAREHOUSE_ID ou use --warehouse-id."
    )


def discover_warehouse(client=None) -> str | None:
    """Last-resort fallback: use the only warehouse the caller can see.

    Databricks Free Edition allows exactly one SQL warehouse, so this turns a
    misconfigured resource binding into a working app instead of a dead end.
    With more than one visible warehouse it declines to guess.
    """
    try:
        if client is None:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient()
        warehouses = [w for w in client.warehouses.list() if getattr(w, "id", None)]
    except Exception as exc:  # noqa: BLE001 - fall through to the config error
        print(f"[config] warehouse discovery failed: {exc}")
        return None

    if len(warehouses) == 1:
        only = warehouses[0]
        print(
            f"[config] no warehouse env var set; using the only visible warehouse "
            f"{only.id} ({getattr(only, 'name', '?')})."
        )
        return only.id

    if len(warehouses) > 1:
        names = ", ".join(f"{w.id} ({getattr(w, 'name', '?')})" for w in warehouses[:5])
        print(f"[config] {len(warehouses)} warehouses visible, refusing to guess: {names}")
    return None


def load_settings(
    warehouse_id: str | None = None,
    discover: bool = False,
) -> Settings:
    """Build settings from the environment.

    `warehouse_id` takes precedence so the CLI can target a warehouse without
    mutating the environment. `discover=True` enables the single-warehouse
    fallback, which the app turns on and the CLI does not.
    """
    resolved_warehouse = (warehouse_id or "").strip() or _warehouse_from_env()
    if not resolved_warehouse and discover:
        resolved_warehouse = discover_warehouse()
    if not resolved_warehouse:
        raise ConfigError(_warehouse_diagnostic())

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
