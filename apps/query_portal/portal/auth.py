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

# Required in app.yaml. Without `sql` the OBO token cannot execute statements and
# the SDK silently falls back to the service principal — the exact failure mode
# `assert_obo_configured` exists to make loud.
REQUIRED_USER_API_SCOPES = ("sql", "iam.current-user:read")


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
