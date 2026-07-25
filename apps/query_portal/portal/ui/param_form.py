"""Renders one widget per parameter and returns the raw values.

Rendering only. Coercion, typing and validation stay in `portal.params`, so the
form and the CLI share one code path and one set of error messages.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from portal.config import Settings
from portal.metadata import QueryDefinition, QueryParameter
from portal.options import load_options
from portal.params import ParamType, resolve_default, today_in


def _default_for(param: QueryParameter, settings: Settings) -> Any:
    """Resolve the configured default, tolerating a bad one.

    A malformed `default_value` is an authoring error in the metadata table; it
    should not stop the user filling the form in by hand.
    """
    try:
        return resolve_default(param, today_in(settings.timezone))
    except Exception as exc:  # noqa: BLE001 - fall back to an empty widget
        st.caption(f"⚠️ Valor padrão inválido em «{param.label}»: {exc}")
        return None


def _label(param: QueryParameter) -> str:
    return f"{param.label} *" if param.is_required else param.label


def render(
    query: QueryDefinition,
    settings: Settings,
    user_client: Any,
) -> dict[str, Any]:
    """Draw the form and return `{param_name: raw_value}`."""
    values: dict[str, Any] = {}

    for param in query.parameters:
        key = f"param::{query.query_id}::{param.param_name}"
        default = _default_for(param, settings)
        help_text = param.help_text or None

        if param.param_type is ParamType.DATE_RANGE:
            # A single widget with a tuple value; params.py splits it into
            # :<name>_inicio and :<name>_fim.
            values[param.param_name] = st.date_input(
                _label(param),
                value=default,
                help=help_text,
                key=key,
                format="DD/MM/YYYY",
            )
            if param.max_range_days:
                st.caption(f"Período máximo: {param.max_range_days} dias.")

        elif param.param_type is ParamType.DATE:
            values[param.param_name] = st.date_input(
                _label(param), value=default, help=help_text, key=key, format="DD/MM/YYYY"
            )

        elif param.param_type is ParamType.INT:
            values[param.param_name] = st.number_input(
                _label(param), value=default, step=1, help=help_text, key=key
            )

        elif param.param_type is ParamType.DECIMAL:
            values[param.param_name] = st.number_input(
                _label(param),
                value=float(default) if default is not None else None,
                help=help_text,
                key=key,
            )

        elif param.param_type in (ParamType.SELECT, ParamType.MULTI_SELECT):
            # Runs as the user: the dropdown can only reveal what they may query.
            options = load_options(user_client, settings, param)
            if param.param_type is ParamType.SELECT:
                index = options.index(default) if default in options else None
                values[param.param_name] = st.selectbox(
                    _label(param),
                    options,
                    index=index,
                    placeholder="Selecione…",
                    help=help_text,
                    key=key,
                )
            else:
                preset = [d for d in (default or []) if d in options]
                values[param.param_name] = st.multiselect(
                    _label(param),
                    options,
                    default=preset,
                    placeholder="Selecione…",
                    help=help_text,
                    key=key,
                )
            if not options:
                st.caption("Nenhuma opção disponível — verifique seu acesso aos dados.")

        else:
            values[param.param_name] = st.text_input(
                _label(param), value=default or "", help=help_text, key=key
            )

    if any(p.is_required for p in query.parameters):
        st.caption("\\* Campo obrigatório.")

    return values
