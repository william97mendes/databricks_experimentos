"""Diagnostics panel.

Scope and identity problems are invisible from the UI and expensive to guess at:
each round trip costs a redeploy. This panel answers "which scopes did the token
actually arrive with?" directly, which is the question every OBO failure reduces
to.

It shows scope *names* and configuration only. The token itself is never
rendered, logged, or copied anywhere.
"""

from __future__ import annotations

import streamlit as st

from portal.auth import SQL_SCOPE, scopes_from_token
from portal.config import Settings


def render(settings: Settings, forwarded_token: str | None, user_email: str) -> None:
    with st.sidebar.expander("🔎 Diagnóstico"):
        st.caption(f"Usuário: `{user_email or '—'}`")

        scopes = scopes_from_token(forwarded_token)
        if scopes is None:
            st.warning(
                "Não foi possível ler os escopos do token. "
                "Ele pode ser opaco — isso não impede o funcionamento."
            )
        elif SQL_SCOPE in scopes:
            st.success(f"Escopo `{SQL_SCOPE}` presente.")
        else:
            st.error(
                f"Escopo `{SQL_SCOPE}` AUSENTE — nenhuma consulta vai executar.\n\n"
                "Adicione o escopo em **Authorization → User authorization → "
                "+ Add scope**, **reinicie o app** e reabra numa sessão nova para "
                "aceitar o novo consentimento."
            )

        if scopes is not None:
            st.caption("Escopos recebidos:")
            st.code("\n".join(sorted(scopes)) or "(nenhum)")

        st.caption("Configuração:")
        st.code(
            "\n".join(
                [
                    f"catalog          = {settings.catalog}",
                    f"schema           = {settings.schema}",
                    f"warehouse_id     = {_masked(settings.warehouse_id)}",
                    f"timezone         = {settings.timezone}",
                    f"group_filtering  = {settings.group_filtering}",
                ]
            )
        )


def _masked(value: str) -> str:
    """Enough to recognise the warehouse, not enough to copy it out of a screenshot."""
    if not value:
        return "(não definido)"
    return value if len(value) <= 6 else f"…{value[-6:]}"
