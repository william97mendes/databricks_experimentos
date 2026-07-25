"""Result table, truncation notice, and the download buttons."""

from __future__ import annotations

import streamlit as st

from portal import results
from portal.audit import AuditLog
from portal.errors import truncation_message
from portal.execution import ExecutionResult
from portal.metadata import QueryDefinition

PREVIEW_ROWS = 1_000


def render(
    query: QueryDefinition,
    result: ExecutionResult,
    audit: AuditLog,
    execution_id: str,
    user_client,
) -> None:
    """Show the result, then offer CSV and XLSX downloads."""
    if result.truncated:
        st.warning(truncation_message(result.row_count))

    if not result.rows:
        st.info("A consulta não retornou linhas para os filtros selecionados.")
        return

    st.success(
        f"{result.row_count:,} linha(s) em {result.duration_ms / 1000:.1f}s".replace(",", ".")
    )

    preview = result.rows[:PREVIEW_ROWS]
    st.dataframe(
        [dict(zip(result.columns, row, strict=False)) for row in preview],
        use_container_width=True,
        hide_index=True,
    )
    if len(result.rows) > PREVIEW_ROWS:
        st.caption(
            f"Exibindo as primeiras {PREVIEW_ROWS} linhas. "
            "O download contém o resultado completo."
        )

    _render_downloads(query, result, audit, execution_id, user_client)

    if result.statement_id:
        st.caption(f"ID da execução: `{result.statement_id}`")


def _render_downloads(
    query: QueryDefinition,
    result: ExecutionResult,
    audit: AuditLog,
    execution_id: str,
    user_client,
) -> None:
    csv_column, xlsx_column = st.columns(2)

    with csv_column:
        st.download_button(
            "⬇️ Baixar CSV",
            data=results.to_csv_bytes(
                result.columns, results.iter_rows(result, user_client)
            ),
            file_name=results.filename(query.query_id, results.FORMAT_CSV),
            mime=results.CONTENT_TYPES[results.FORMAT_CSV],
            use_container_width=True,
            on_click=lambda: audit.record_download(execution_id, results.FORMAT_CSV),
        )

    with xlsx_column:
        st.download_button(
            "⬇️ Baixar Excel",
            data=results.to_xlsx_bytes(
                result.columns, results.iter_rows(result, user_client)
            ),
            file_name=results.filename(query.query_id, results.FORMAT_XLSX),
            mime=results.CONTENT_TYPES[results.FORMAT_XLSX],
            use_container_width=True,
            on_click=lambda: audit.record_download(execution_id, results.FORMAT_XLSX),
        )
