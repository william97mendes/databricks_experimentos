"""Error taxonomy and the mapping to user-facing Portuguese copy.

Nothing raw ever reaches the user. `to_user_message` returns text a business user
can act on; the original exception is logged and the statement_id is shown so the
data team can trace it in `system.query.history`.
"""

from __future__ import annotations

import re

# UI copy is Portuguese; code and comments are English.
MSG_PERMISSION_DENIED = (
    "Você não tem acesso a esta consulta, solicite ao time de dados."
)
MSG_TIMEOUT = "A consulta excedeu o tempo limite, reduza o período consultado."
# A statement stuck in PENDING never started running: the warehouse was starting
# or queued. Telling that user to "reduce the period" would send them chasing a
# problem they do not have.
MSG_WAREHOUSE_UNAVAILABLE = (
    "O SQL warehouse não ficou disponível a tempo. Ele pode estar iniciando — "
    "aguarde alguns instantes e execute novamente."
)
MSG_INTERNAL = (
    "Erro interno do portal. Avise o time de dados e informe o identificador da execução."
)
# Deployment problem, not a data-permission problem: the distinction matters
# because "solicite acesso aos dados" would send the user to the wrong team.
MSG_MISSING_SCOPE = (
    "O portal não está autorizado a executar consultas em seu nome. "
    "Isso é uma configuração do aplicativo — avise o time de dados."
)
MSG_CANCELLED = "A execução foi cancelada."
MSG_OBJECT_NOT_FOUND = (
    "A tabela usada por esta consulta não está disponível. Avise o time de dados."
)
MSG_GENERIC = "Não foi possível executar a consulta. Tente novamente ou avise o time de dados."


class PortalError(Exception):
    """Base for errors already carrying user-safe Portuguese copy."""

    user_message: str = MSG_GENERIC

    def __init__(self, user_message: str | None = None, *, technical: str | None = None):
        self.user_message = user_message or self.user_message
        self.technical = technical or self.user_message
        super().__init__(self.technical)


class ValidationError(PortalError):
    """User input rejected before submission. Message is always user-safe."""


class ConfigurationError(PortalError):
    """Deployment is misconfigured — surfaced loudly, not mapped to friendly copy."""


class AuthError(PortalError):
    """The forwarded user token is missing or unusable."""


class ExecutionTimeout(PortalError):
    user_message = MSG_TIMEOUT


class WarehouseUnavailable(PortalError):
    """Timed out while the statement was still PENDING — it never began running."""

    user_message = MSG_WAREHOUSE_UNAVAILABLE


class InternalError(PortalError):
    """A defect in the portal itself, not something the user can act on."""

    user_message = MSG_INTERNAL


class PermissionDenied(PortalError):
    user_message = MSG_PERMISSION_DENIED


_PERMISSION_PATTERNS = (
    r"permission[_ ]?denied",
    "does not have",
    "insufficient privileges",
    "access denied",
    "requires .* privilege",
)

# The app is missing an OAuth scope, which is a deployment problem rather than a
# data-access one. It arrives as 403 "Invalid scope, required scopes: sql", and
# the SDK often cannot parse that body, so match the text directly.
_SCOPE_PATTERNS = (
    "invalid scope",
    "required scopes",
    "does not have required scopes",
)
_NOT_FOUND_PATTERNS = (
    "table_or_view_not_found",
    "schema_not_found",
    "does not exist",
    "cannot be found",
)

# Deliberately NOT a bare "timeout". This module receives exception text that can
# contain our own keyword argument names — `wait_timeout` and `on_wait_timeout`
# are passed on every execute_statement call — and matching those would report a
# programming error to the user as "reduza o período", which is unactionable and
# wrong. Match only phrases that describe something actually timing out.
# Separators are flexible because the same condition arrives as prose
# ("deadline exceeded") and as an error code ("DEADLINE_EXCEEDED").
_TIMEOUT_PATTERNS = (
    r"timed[ _]out",
    r"deadline[ _]exceeded",
    r"execution[ _]timeout",
    r"statement[ _]timeout",
    r"query[ _]timeout",
    r"timeout[ _]expired",
    r"exceeded (the )?(execution )?time limit",
)

# Exception types that are always defects in this codebase rather than something
# the user did. They must never be dressed up as friendly advice.
_PROGRAMMING_ERRORS = (TypeError, AttributeError, NameError, ImportError, IndentationError)


def _matches(haystack: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, haystack) for p in patterns)


def to_user_message(exc: BaseException) -> str:
    """Map any exception to actionable Portuguese copy.

    Errors from the Statement Execution API arrive as SDK exceptions or as an
    error string on the status payload, so this matches on text rather than on
    SDK exception classes alone.
    """
    if isinstance(exc, PortalError):
        return exc.user_message

    # Checked before any text matching: a TypeError about `on_wait_timeout`
    # contains the word "timeout" but is a bug here, not a slow query.
    if isinstance(exc, _PROGRAMMING_ERRORS):
        return MSG_INTERNAL

    text = f"{type(exc).__name__}: {exc}".lower()
    # Checked before the permission patterns: a missing scope also reports as
    # PermissionDenied, but the fix is a deployment change, not a UC grant.
    if _matches(text, _SCOPE_PATTERNS):
        return MSG_MISSING_SCOPE
    if _matches(text, _PERMISSION_PATTERNS):
        return MSG_PERMISSION_DENIED
    if _matches(text, _TIMEOUT_PATTERNS):
        return MSG_TIMEOUT
    if _matches(text, _NOT_FOUND_PATTERNS):
        return MSG_OBJECT_NOT_FOUND
    return MSG_GENERIC


def message_for_status(error_message: str | None) -> str:
    """Map the `status.error.message` of a FAILED statement to Portuguese copy."""
    text = (error_message or "").lower()
    if _matches(text, _SCOPE_PATTERNS):
        return MSG_MISSING_SCOPE
    if _matches(text, _PERMISSION_PATTERNS):
        return MSG_PERMISSION_DENIED
    if _matches(text, _TIMEOUT_PATTERNS):
        return MSG_TIMEOUT
    if _matches(text, _NOT_FOUND_PATTERNS):
        return MSG_OBJECT_NOT_FOUND
    return MSG_GENERIC


def truncation_message(row_count: int) -> str:
    """Explicit, actionable notice that the result hit `max_rows`."""
    return (
        f"O resultado foi truncado em {row_count:,} linhas. ".replace(",", ".")
        + "Reduza o período ou aplique mais filtros para ver todos os dados."
    )
