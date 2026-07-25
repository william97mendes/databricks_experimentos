"""Portal de consultas — Databricks App entry point.

Deliberately thin: startup assertion, identity wiring, and tab layout. All logic
lives in `portal/`, which is unit tested without a Streamlit runtime.
"""

from __future__ import annotations

import streamlit as st
from portal import execution as execution_module
from portal.audit import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    STATUS_TIMEOUT,
    AuditLog,
    ExecutionRecord,
    finished,
    started,
)
from portal.auth import identity_from_headers
from portal.config import ConfigError, load_settings
from portal.errors import ExecutionTimeout, PortalError, to_user_message
from portal.groups import GroupResolver
from portal.metadata import MetadataRepository
from portal.params import bind
from portal.ui import catalog_view, history_view, param_form, result_view

st.set_page_config(page_title="Portal de consultas", page_icon="📊", layout="wide")


@st.cache_resource
def _settings():
    # discover=True lets a misconfigured warehouse binding fall back to the only
    # warehouse the app can see, which is always the case on Free Edition.
    return load_settings(discover=True)


def _fatal(message: str, detail: str | None = None) -> None:
    """Stop the app with an operator-facing error.

    Misconfiguration is shown in full rather than mapped to friendly copy: the
    person who can fix it needs the detail, and the app must not serve traffic
    in a degraded auth state.
    """
    st.error(message)
    if detail:
        st.code(detail)
    st.stop()


def main() -> None:
    try:
        settings = _settings()
    except ConfigError as exc:
        _fatal("Configuração inválida do aplicativo.", str(exc))
        return

    # Fails loudly when the forwarded user token is missing. Without it queries
    # would silently run as the service principal, which is the one failure this
    # app must never degrade into.
    try:
        identity = identity_from_headers(st.context.headers)
    except PortalError as exc:
        _fatal(
            "O aplicativo não está configurado para executar consultas em nome do usuário.",
            f"{exc.technical}\n\n"
            "Verifique 'user_api_scopes: [sql, iam.current-user:read]' em app.yaml.",
        )
        return

    user_client = identity.user_client()
    sp_client = identity.service_principal_client()
    repo = MetadataRepository(sp_client, settings)
    audit = AuditLog(sp_client, settings)
    resolver = GroupResolver(user_client, settings)

    st.title("📊 Portal de consultas")
    st.caption(f"Conectado como **{identity.user_email or 'usuário autenticado'}**")

    query = catalog_view.render(repo, settings, user_client, resolver, identity.user_email)

    consultar_tab, historico_tab = st.tabs(["Consultar", "Minhas execuções"])

    with consultar_tab:
        if query is None:
            st.info("Selecione uma consulta na barra lateral.")
        else:
            _render_query(query, settings, repo, audit, identity, user_client)

    with historico_tab:
        history_view.render(audit, settings, identity.user_email)


def _render_query(query, settings, repo, audit, identity, user_client) -> None:
    # The list view omits parameters; load the full definition on selection.
    full = repo.get_query(query.query_id)
    catalog_view.render_header(full)

    with st.form(key=f"form::{full.query_id}"):
        raw_values = param_form.render(full, settings, user_client)
        submitted = st.form_submit_button("▶️ Executar", type="primary")

    if not submitted:
        return

    # Validation happens before submission, so the user is never told the period
    # is too wide only after waiting for the warehouse.
    try:
        bound = bind(full.parameters, raw_values)
    except PortalError as exc:
        st.error(exc.user_message)
        return

    execution_id, started_at = started()
    warehouse_id = full.effective_warehouse(settings)

    with st.spinner("Executando a consulta…"):
        try:
            result = execution_module.execute(
                user_client,
                settings,
                statement=full.sql_template,
                warehouse_id=warehouse_id,
                parameters=bound.items,
                max_rows=full.effective_max_rows(settings),
                timeout_seconds=full.effective_timeout(settings),
            )
        except PortalError as exc:
            _record_failure(audit, full, identity, bound, execution_id, started_at,
                            warehouse_id, exc)
            _show_error(exc.user_message, exc, execution_id)
            return
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            _record_failure(audit, full, identity, bound, execution_id, started_at,
                            warehouse_id, exc)
            _show_error(to_user_message(exc), exc, execution_id)
            return

    audit.record(
        ExecutionRecord(
            execution_id=execution_id,
            query_id=full.query_id,
            user_email=identity.user_email,
            parameters=bound.recorded,
            status=STATUS_SUCCEEDED,
            started_at=started_at,
            ended_at=finished(),
            statement_id=result.statement_id,
            warehouse_id=warehouse_id,
            row_count=result.row_count,
            duration_ms=result.duration_ms,
        )
    )

    result_view.render(full, result, audit, execution_id, user_client)


def _show_error(user_message: str, exc: BaseException, execution_id: str) -> None:
    """Friendly copy up front, the underlying message one click away.

    Business users read the first line and stop. Whoever has to fix it needs the
    technical text — without it, every failure becomes a support ticket. This is
    the error message, never a stack trace.
    """
    st.error(user_message)
    technical = getattr(exc, "technical", None) or f"{type(exc).__name__}: {exc}"
    with st.expander("Detalhes técnicos"):
        st.code(str(technical))
        st.caption(
            f"Identificador da execução: `{execution_id}` — informe este código ao "
            "time de dados."
        )


def _record_failure(
    audit, query, identity, bound, execution_id, started_at, warehouse_id, exc
) -> None:
    status = STATUS_TIMEOUT if isinstance(exc, ExecutionTimeout) else STATUS_FAILED
    technical = getattr(exc, "technical", f"{type(exc).__name__}: {exc}")
    audit.record(
        ExecutionRecord(
            execution_id=execution_id,
            query_id=query.query_id,
            user_email=identity.user_email,
            parameters=bound.recorded,
            status=status,
            started_at=started_at,
            ended_at=finished(),
            warehouse_id=warehouse_id,
            error_message=str(technical)[:4000],
        )
    )


main()
