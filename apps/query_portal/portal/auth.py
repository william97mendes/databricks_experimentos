"""The two-identity boundary.

Every *published* query, and every `options_sql` dropdown query, runs with the end
user's forwarded OAuth token — Unity Catalog is the authorization boundary, not
this app. The service principal is used only to read the metadata tables and to
write the audit log.

`allowed_groups` filtering is UX. It decides what a user sees in the list; it does
not decide what a user may read. UC grants do.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from databricks.sdk import WorkspaceClient

from portal.errors import AuthError, ConfigurationError

# Databricks Apps forwards the end user's OAuth token in this header when
# `user_api_scopes` is declared in app.yaml.
FORWARDED_TOKEN_HEADER = "x-forwarded-access-token"
FORWARDED_EMAIL_HEADER = "x-forwarded-email"

# Without `sql` the forwarded token cannot execute statements: the API answers
# 403 "Invalid scope, required scopes: sql", which the SDK surfaces as an
# unparseable PermissionDenied. `assert_obo_configured` checks the token's own
# scope claim so that failure is reported at startup, in plain language.
REQUIRED_USER_API_SCOPES = ("sql", "iam.current-user:read")
SQL_SCOPE = "sql"

# The scope set Databricks grants when an app declares no scopes at all. Seeing
# exactly this in a token is the signature of scopes never having been applied.
DEFAULT_FALLBACK_SCOPES = frozenset({"iam.access-control:read", "iam.current-user:read"})


def scopes_from_token(token: str | None) -> frozenset[str] | None:
    """Read the `scope` claim from a JWT access token.

    Returns `None` when the scopes cannot be determined (opaque token, unexpected
    shape) so callers can proceed rather than block on a parsing detail.

    The signature is deliberately not verified: the platform issued this token to
    us over a trusted channel, and the goal is a readable diagnostic, not an
    authorization decision. The token itself is never logged or returned.
    """
    if not token or token.count(".") != 2:
        return None

    import base64
    import json

    try:
        payload_segment = token.split(".")[1]
        padding = "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding))
    except Exception:  # noqa: BLE001 - undeterminable, not fatal
        return None

    raw = payload.get("scope") or payload.get("scopes")
    # An absent or empty claim is undeterminable, not "no scopes": blocking
    # startup on a token shape we do not recognise would be worse than trying.
    if not raw:
        return None
    if isinstance(raw, str):
        parsed = frozenset(s for s in raw.split() if s)
        return parsed or None
    if isinstance(raw, (list, tuple)):
        parsed = frozenset(str(s) for s in raw if str(s))
        return parsed or None
    return None


def assert_sql_scope(token: str | None) -> None:
    """Fail loudly when the forwarded token lacks the `sql` scope.

    Without this the app starts, lists queries, and then fails on every
    execution with an opaque 403 — which is exactly what happened in the first
    deployment. Checking here names the cause and the fix.
    """
    scopes = scopes_from_token(token)
    if scopes is None or SQL_SCOPE in scopes:
        return

    present = ", ".join(sorted(scopes)) or "(nenhum)"
    only_defaults = scopes and set(scopes) <= set(DEFAULT_FALLBACK_SCOPES)
    diagnosis = (
        "O token contém apenas os escopos padrão, ou seja, nenhum escopo foi "
        "aplicado ao app.\n"
        if only_defaults
        else ""
    )

    raise ConfigurationError(
        technical=(
            f"O token do usuário não possui o escopo '{SQL_SCOPE}'.\n"
            f"Escopos presentes: {present}\n"
            f"{diagnosis}\n"
            "Como corrigir (a ordem importa):\n"
            "1. Um admin do workspace precisa habilitar 'User authorization' "
            "(Public Preview) e permitir o escopo 'sql' em "
            "Settings > Development > Apps > 'Restrict OAuth scopes for apps'.\n"
            "2. Reinicie o app: só é possível adicionar escopos depois disso.\n"
            "3. Edite o app na UI: aba 'Authorization' > User authorization > "
            "'+ Add scope' > 'sql'. Declarar user_api_scopes no app.yaml não "
            "substitui esse passo.\n"
            "4. Faça deploy/restart novamente e reabra o app: o Databricks vai "
            "pedir um novo consentimento para o escopo adicionado.\n"
            "Recusando iniciar: sem o escopo 'sql', toda consulta falharia com "
            "403 'Invalid scope'."
        )
    )


class Identity(Protocol):
    """Supplies the two clients the app needs, and the caller's email."""

    def user_client(self) -> WorkspaceClient:
        """Client authenticated as the end user. Runs all published queries."""

    def service_principal_client(self) -> WorkspaceClient:
        """Client authenticated as the app. Metadata reads and audit writes only."""

    @property
    def user_email(self) -> str: ...


def running_in_databricks_app() -> bool:
    """True when executing inside the Databricks Apps runtime."""
    return bool(os.environ.get("DATABRICKS_APP_NAME") or os.environ.get("DATABRICKS_APP_PORT"))


class AppIdentity:
    """Identity inside Databricks Apps: forwarded user token + app service principal."""

    def __init__(
        self,
        forwarded_token: str | None,
        user_email: str | None,
        host: str | None = None,
    ):
        assert_obo_configured(forwarded_token)
        # A present token is not enough: it must carry the `sql` scope.
        assert_sql_scope(forwarded_token)
        self._token = forwarded_token or ""
        self._email = (user_email or "").strip()
        self._host = host or os.environ.get("DATABRICKS_HOST", "")
        self._sp: WorkspaceClient | None = None

    def user_client(self) -> WorkspaceClient:
        # A fresh client per request: the forwarded token is per-request and
        # must never be cached across users.
        return WorkspaceClient(host=self._host, token=self._token, auth_type="pat")

    def service_principal_client(self) -> WorkspaceClient:
        if self._sp is None:
            # Default credential chain resolves the app's own service principal.
            self._sp = WorkspaceClient()
        return self._sp

    @property
    def user_email(self) -> str:
        return self._email


class CliIdentity:
    """Identity for the phase-1 CLI: the developer's own OAuth profile.

    The developer *is* the end user here, so `user_client` genuinely runs queries
    under a human identity and exercises the same UC path the app will. The
    service principal client is the same profile locally — the split only becomes
    real once deployed, which is why audit writes are read-only-checked in tests
    rather than exercised here.
    """

    def __init__(self, profile: str | None = None):
        self._profile = profile
        self._client: WorkspaceClient | None = None
        self._email: str | None = None

    def _resolve(self) -> WorkspaceClient:
        if self._client is None:
            self._client = (
                WorkspaceClient(profile=self._profile) if self._profile else WorkspaceClient()
            )
        return self._client

    def user_client(self) -> WorkspaceClient:
        return self._resolve()

    def service_principal_client(self) -> WorkspaceClient:
        return self._resolve()

    @property
    def user_email(self) -> str:
        if self._email is None:
            me = self._resolve().current_user.me()
            self._email = me.user_name or ""
        return self._email


def identity_from_headers(headers: Any) -> AppIdentity:
    """Build an `AppIdentity` from the incoming request headers.

    Takes a plain mapping rather than importing Streamlit, so the auth boundary
    stays unit-testable without a UI runtime. Header lookup is case-insensitive
    because proxies do not agree on casing.
    """
    lowered = {str(k).lower(): v for k, v in dict(headers or {}).items()}
    return AppIdentity(
        forwarded_token=lowered.get(FORWARDED_TOKEN_HEADER),
        user_email=lowered.get(FORWARDED_EMAIL_HEADER),
    )


def assert_obo_configured(forwarded_token: str | None) -> None:
    """Fail loudly when the forwarded user token is absent.

    Without this the app keeps working — as the service principal — which would
    silently execute every user's query with the app's privileges. That is the
    one failure this app must never degrade into, so it is fatal at startup.
    """
    if forwarded_token:
        return
    if not running_in_databricks_app():
        raise AuthError(
            technical=(
                "No forwarded user token available outside the Databricks Apps runtime. "
                "Use CliIdentity for local execution."
            )
        )
    raise ConfigurationError(
        technical=(
            f"Header {FORWARDED_TOKEN_HEADER!r} is missing. Declare "
            f"user_api_scopes: {list(REQUIRED_USER_API_SCOPES)} in app.yaml and redeploy. "
            "Refusing to start: without it, queries would run as the service principal."
        )
    )
