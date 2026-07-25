"""The `sql` scope check.

Regression cover for a real deployment failure: the app started, listed queries,
and then failed every execution with 403 "Invalid scope, required scopes: sql".
The forwarded token carried only the default scope set, meaning no scopes had
been applied to the app at all.
"""

from __future__ import annotations

import base64
import json

import pytest
from portal.auth import (
    DEFAULT_FALLBACK_SCOPES,
    assert_sql_scope,
    scopes_from_token,
)
from portal.errors import MSG_MISSING_SCOPE, ConfigurationError, to_user_message


def make_jwt(scope: str | list[str] | None, claim: str = "scope") -> str:
    """Build a JWT-shaped token. Only the payload segment is ever read."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"at+jwt"}').decode().rstrip("=")
    body = {"sub": "ana@empresa.com.br"}
    if scope is not None:
        body[claim] = scope
    payload = (
        base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    )
    return f"{header}.{payload}.fake-signature"


# The exact scope set observed in the failing deployment.
OBSERVED_FAILING_SCOPES = (
    "offline_access email iam.current-user:read openid iam.access-control:read profile"
)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_reads_space_delimited_scope_claim():
    scopes = scopes_from_token(make_jwt("sql iam.current-user:read"))
    assert scopes == frozenset({"sql", "iam.current-user:read"})


def test_reads_a_list_valued_claim():
    assert scopes_from_token(make_jwt(["sql", "files"])) == frozenset({"sql", "files"})


def test_reads_the_alternate_scopes_claim_name():
    assert scopes_from_token(make_jwt("sql", claim="scopes")) == frozenset({"sql"})


def test_payload_is_decoded_without_padding_errors():
    """Base64url segments in real tokens arrive unpadded."""
    for length in range(1, 40):
        token = make_jwt("sql " + "x" * length)
        assert scopes_from_token(token) is not None


@pytest.mark.parametrize("token", [None, "", "opaque-token", "a.b", "a.b.c.d"])
def test_undeterminable_tokens_return_none(token):
    assert scopes_from_token(token) is None


def test_malformed_payload_returns_none_instead_of_raising():
    assert scopes_from_token("header.!!!not-base64!!!.sig") is None


def test_missing_claim_returns_none():
    assert scopes_from_token(make_jwt(None)) is None


# --------------------------------------------------------------------------- #
# The assertion
# --------------------------------------------------------------------------- #


def test_a_token_with_sql_passes():
    assert_sql_scope(make_jwt("sql iam.current-user:read"))  # must not raise


def test_the_observed_failing_token_is_rejected():
    with pytest.raises(ConfigurationError) as exc:
        assert_sql_scope(make_jwt(OBSERVED_FAILING_SCOPES))

    assert "'sql'" in exc.value.technical


def test_rejection_lists_the_scopes_that_were_present():
    with pytest.raises(ConfigurationError) as exc:
        assert_sql_scope(make_jwt(OBSERVED_FAILING_SCOPES))

    technical = exc.value.technical
    assert "iam.current-user:read" in technical
    assert "openid" in technical


def test_rejection_never_echoes_the_token():
    """Tokens must not be logged; the diagnostic reports scopes only."""
    token = make_jwt(OBSERVED_FAILING_SCOPES)

    with pytest.raises(ConfigurationError) as exc:
        assert_sql_scope(token)

    assert token not in exc.value.technical
    assert "fake-signature" not in exc.value.technical


def test_default_only_scopes_are_diagnosed_as_scopes_never_applied():
    """This is the signature of an app that declared no scopes."""
    with pytest.raises(ConfigurationError) as exc:
        assert_sql_scope(make_jwt(" ".join(sorted(DEFAULT_FALLBACK_SCOPES))))

    assert "nenhum escopo foi aplicado" in exc.value.technical


def test_rejection_explains_that_app_yaml_alone_is_insufficient():
    """The guide originally claimed app.yaml was enough, which cost a debug cycle."""
    with pytest.raises(ConfigurationError) as exc:
        assert_sql_scope(make_jwt(OBSERVED_FAILING_SCOPES))

    technical = exc.value.technical
    assert "user_api_scopes" in technical
    assert "Add scope" in technical


def test_an_undeterminable_token_does_not_block_startup():
    """An opaque token must not be treated as a missing scope."""
    assert_sql_scope("opaque-token")  # must not raise


# --------------------------------------------------------------------------- #
# Runtime mapping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message",
    [
        "Invalid scope, required scopes: sql",
        "Provided OAuth token does not have required scopes",
        "PermissionDenied: unable to parse response ... Invalid scope, required scopes: sql",
    ],
)
def test_scope_errors_are_not_reported_as_a_data_permission_problem(message):
    """"Solicite acesso aos dados" would send the user to the wrong team."""
    assert to_user_message(RuntimeError(message)) == MSG_MISSING_SCOPE


def test_a_genuine_data_permission_error_still_maps_to_the_access_message():
    from portal.errors import MSG_PERMISSION_DENIED

    assert to_user_message(
        RuntimeError("PermissionDenied: User does not have SELECT on table t")
    ) == MSG_PERMISSION_DENIED
