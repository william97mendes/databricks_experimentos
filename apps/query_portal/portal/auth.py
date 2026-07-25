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
            "CAUSA MAIS COMUM: o consentimento foi concedido ANTES de o escopo "
            "'sql' existir no app. O Databricks guarda esse consentimento e o "
            "usuário NAO consegue revoga-lo, entao o token continua sendo "
            "emitido com os escopos aprovados originalmente. Adicionar o escopo "
            "ao app nao basta: e preciso forcar um novo consentimento.\n\n"
            "Como corrigir, nesta ordem:\n"
            "1. Confirme o escopo 'sql' no app: aba 'Authorization' > "
            "User authorization > '+ Add scope'. Declarar user_api_scopes no "
            "app.yaml NAO substitui esse passo.\n"
            "2. PARE o app e inicie de novo (Stop, depois Run). Um simples "
            "redeploy NAO dispara o novo consentimento — tem que ser stop/start.\n"
            "3. Abra o app: a tela de consentimento deve aparecer pedindo o "
            "escopo novo. Aceite.\n"
            "4. Se a tela NAO aparecer: saia da sua conta Databricks, limpe a "
            "sessao do navegador (ou use uma janela anonima) e abra o app de "
            "novo. Este e o passo mais esquecido.\n"
            "5. Ultimo recurso: exclua e recrie o app. Recriar zera os escopos "
            "ja consentidos. Na Free Edition nao ha console de conta, entao nao "
            "existe outra forma de limpar um consentimento antigo.\n\n"
            "Verifique tambem, como admin, se o escopo 'sql' esta liberado em "
            "Settings > Development > Apps > 'Restrict OAuth scopes for apps' "
            "(o padrao 'All APIs' libera tudo; 'None' desativa a autorizacao "
            "de usuario).\n\n"
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
        #
        # The app runtime also exports DATABRICKS_CLIENT_ID/SECRET for the
        # service principal. The SDK's Config picks those up from the
        # environment even when a token is passed explicitly, so `auth_type` is
        # pinned to "pat" and then verified below: if the SDK ever resolved to
        # the OAuth client-credentials strategy instead, every published query
        # would silently run as the service principal.
        client = WorkspaceClient(host=self._host, token=self._token, auth_type="pat")
        _assert_authenticating_as_user(client)
        return client

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


def _assert_authenticating_as_user(client: WorkspaceClient) -> None:
    """Guarantee the client carries the forwarded token, not the app's identity.

    This protects the single invariant the whole design rests on: Unity Catalog
    can only be the authorization boundary if queries actually run as the user.
    A client that fell back to the service principal would still work — which is
    exactly what makes the failure dangerous.
    """
    auth_type = getattr(getattr(client, "config", None), "auth_type", None)
    if auth_type != "pat":
        raise ConfigurationError(
            technical=(
                f"O cliente do usuário resolveu auth_type={auth_type!r} em vez de 'pat'. "
                "As consultas seriam executadas como o service principal do app, "
                "ignorando as permissões do usuário no Unity Catalog. Execução bloqueada."
            )
        )


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
