"""The two-identity boundary and the user-scoped dropdown queries."""

from __future__ import annotations

import pytest
from databricks.sdk.service.sql import StatementState
from portal.auth import (
    FORWARDED_TOKEN_HEADER,
    REQUIRED_USER_API_SCOPES,
    assert_obo_configured,
)
from portal.errors import AuthError, ConfigurationError
from portal.options import load_options

from tests.conftest import FakeWorkspaceClient, make_param, response

# --------------------------------------------------------------------------- #
# OBO assertion
# --------------------------------------------------------------------------- #


def test_required_scopes_are_declared():
    """app.yaml must request both; `sql` is what makes OBO execution possible."""
    assert set(REQUIRED_USER_API_SCOPES) == {"sql", "iam.current-user:read"}


def test_a_present_token_passes():
    assert_obo_configured("token-abc")  # must not raise


def test_missing_token_inside_the_app_runtime_is_fatal(monkeypatch):
    """The app must refuse to serve rather than fall back to the service principal."""
    monkeypatch.setenv("DATABRICKS_APP_NAME", "query-portal")

    with pytest.raises(ConfigurationError) as exc:
        assert_obo_configured(None)

    assert FORWARDED_TOKEN_HEADER in exc.value.technical
    assert "user_api_scopes" in exc.value.technical


def test_missing_token_outside_the_app_runtime_points_at_the_cli(monkeypatch):
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)

    with pytest.raises(AuthError) as exc:
        assert_obo_configured(None)

    assert "CliIdentity" in exc.value.technical


def test_identity_from_headers_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    from portal.auth import identity_from_headers

    identity = identity_from_headers(
        {"X-Forwarded-Access-Token": "tok", "X-Forwarded-Email": "ana@empresa.com.br"}
    )

    assert identity.user_email == "ana@empresa.com.br"


def test_identity_from_headers_rejects_a_missing_token(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_NAME", "query-portal")
    from portal.auth import identity_from_headers

    with pytest.raises(ConfigurationError):
        identity_from_headers({"x-forwarded-email": "ana@empresa.com.br"})


# --------------------------------------------------------------------------- #
# Dropdown options
# --------------------------------------------------------------------------- #


def options_response(values):
    return response(
        state=StatementState.SUCCEEDED,
        columns=["valor"],
        rows=[[v] for v in values],
    )


def test_static_options_need_no_round_trip(settings):
    client = FakeWorkspaceClient([options_response([])])
    param = make_param("uf", options_static=["SP", "RJ"])

    assert load_options(client, settings, param) == ["SP", "RJ"]
    assert client.statement_execution.calls == []


def test_options_sql_runs_on_the_supplied_client(settings):
    """That client is the user's: a dropdown must not reveal unauthorized values."""
    client = FakeWorkspaceClient([options_response(["10001", "10002"])])
    param = make_param("cep", options_sql="SELECT DISTINCT pickup_zip FROM t")

    assert load_options(client, settings, param) == ["10001", "10002"]
    assert len(client.statement_execution.calls) == 1


def test_options_query_takes_no_parameters(settings):
    client = FakeWorkspaceClient([options_response(["A"])])
    param = make_param("x", options_sql="SELECT DISTINCT a FROM t")

    load_options(client, settings, param)

    assert not client.statement_execution.calls[0].get("parameters")


def test_a_failing_dropdown_yields_no_options_instead_of_breaking_the_form(settings):
    class Exploding:
        def execute_statement(self, **_):
            raise RuntimeError("PERMISSION_DENIED")

    class Client:
        statement_execution = Exploding()

    param = make_param("cep", options_sql="SELECT DISTINCT pickup_zip FROM t")
    assert load_options(Client(), settings, param) == []


def test_no_options_configured_returns_empty(settings):
    client = FakeWorkspaceClient([options_response([])])
    assert load_options(client, settings, make_param("x")) == []
