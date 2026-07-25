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


class PermissionDenied(PortalError):
    user_message = MSG_PERMISSION_DENIED


_PERMISSION_PATTERNS = (
    "permission_denied",
    "does not have",
    "insufficient privileges",
    "access denied",
    "requires .* privilege",
)
_NOT_FOUND_PATTERNS = (
    "table_or_view_not_found",
    "schema_not_found",
    "does not exist",
    "cannot be found",
)


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

    text = f"{type(exc).__name__}: {exc}".lower()
    if _matches(text, _PERMISSION_PATTERNS):
        return MSG_PERMISSION_DENIED
    if "timeout" in text or "deadline" in text:
        return MSG_TIMEOUT
    if _matches(text, _NOT_FOUND_PATTERNS):
        return MSG_OBJECT_NOT_FOUND
    return MSG_GENERIC


def message_for_status(error_message: str | None) -> str:
    """Map the `status.error.message` of a FAILED statement to Portuguese copy."""
    text = (error_message or "").lower()
    if _matches(text, _PERMISSION_PATTERNS):
        return MSG_PERMISSION_DENIED
    if "timeout" in text or "exceeded" in text:
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
