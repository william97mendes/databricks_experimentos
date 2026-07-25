"""Catalog list: what the current user may see, grouped by category."""

from __future__ import annotations

import streamlit as st

from portal.config import Settings
from portal.groups import GroupResolver, visible_for_user
from portal.metadata import MetadataRepository, QueryDefinition


@st.cache_data(ttl=300, show_spinner=False)
def _load_catalog(_repo: MetadataRepository, _cache_key: str) -> list[QueryDefinition]:
    """Cache the catalog per session.

    Publishing is an INSERT, so a short TTL is what makes a new query appear
    without a redeploy.
    """
    return _repo.list_queries()


def render(
    repo: MetadataRepository,
    settings: Settings,
    user_client,
    resolver: GroupResolver,
    user_email: str,
) -> QueryDefinition | None:
    """Draw the sidebar picker and return the selected query, if any."""
    queries = _load_catalog(repo, user_email)
    visible = visible_for_user(user_client, settings, queries, resolver)

    st.sidebar.header("Consultas")

    if resolver.last_error:
        st.sidebar.caption(
            "⚠️ Não foi possível verificar seus grupos; consultas restritas "
            "podem não aparecer."
        )

    if not visible:
        st.sidebar.info("Nenhuma consulta disponível para o seu usuário.")
        return None

    by_category: dict[str, list[QueryDefinition]] = {}
    for query in visible:
        by_category.setdefault(query.category or "Geral", []).append(query)

    labels: dict[str, QueryDefinition] = {}
    for category in sorted(by_category):
        for query in by_category[category]:
            labels[f"{category} › {query.title}"] = query

    choice = st.sidebar.radio(
        "Selecione uma consulta",
        list(labels),
        label_visibility="collapsed",
    )

    hidden = len(queries) - len(visible)
    if hidden > 0:
        st.sidebar.caption(
            f"{hidden} consulta(s) não exibida(s) por restrição de grupo."
        )
    if not settings.group_filtering:
        st.sidebar.caption("Filtro por grupo desativado nesta instalação.")

    return labels.get(choice)


def render_header(query: QueryDefinition) -> None:
    st.subheader(query.title)
    if query.description:
        st.write(query.description)
    meta = [f"`{query.query_id}`"]
    if query.owner_email:
        meta.append(f"Responsável: {query.owner_email}")
    st.caption(" · ".join(meta))
