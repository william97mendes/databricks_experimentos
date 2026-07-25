"""«Minhas execuções».

Consumer-access users cannot open Query History, so this tab is the only window
they have into what they ran. Rows are read by the service principal and filtered
to the authenticated caller.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from portal.audit import AuditLog
from portal.config import Settings

_STATUS_ICON = {
    "SUCCEEDED": "✅",
    "FAILED": "❌",
    "TIMEOUT": "⏱️",
    "CANCELED": "🚫",
}


def _format_parameters(raw: Any) -> str:
    if not raw:
        return "—"
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return str(raw)
    if not isinstance(parsed, dict) or not parsed:
        return "—"
    return ", ".join(f"{k}={v}" for k, v in parsed.items() if v is not None) or "—"


def _duration(raw: Any) -> str:
    try:
        return f"{int(raw) / 1000:.1f}s"
    except (TypeError, ValueError):
        return "—"


def _rows(raw: Any) -> str:
    try:
        return f"{int(raw):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def render(audit: AuditLog, settings: Settings, user_email: str) -> None:
    st.subheader("Minhas execuções")
    st.caption(f"Últimas {settings.history_limit} execuções de {user_email}.")

    try:
        executions = audit.recent_for_user(user_email)
    except Exception as exc:  # noqa: BLE001 - the tab must not break the app
        st.info("Não foi possível carregar seu histórico agora.")
        st.caption(f"Detalhe técnico: {type(exc).__name__}")
        return

    if not executions:
        st.info("Você ainda não executou nenhuma consulta.")
        return

    st.dataframe(
        [
            {
                "": _STATUS_ICON.get(str(row.get("status")), "•"),
                "Consulta": row.get("query_id"),
                "Parâmetros": _format_parameters(row.get("parameters")),
                "Linhas": _rows(row.get("row_count")),
                "Duração": _duration(row.get("duration_ms")),
                "Download": row.get("downloaded_format") or "—",
                "Início": row.get("started_at"),
            }
            for row in executions
        ],
        use_container_width=True,
        hide_index=True,
    )

    failures = [r for r in executions if str(r.get("status")) != "SUCCEEDED"]
    if failures:
        with st.expander(f"Execuções com problema ({len(failures)})"):
            for row in failures:
                st.write(
                    f"**{row.get('query_id')}** — {row.get('status')} "
                    f"em {row.get('started_at')}"
                )
