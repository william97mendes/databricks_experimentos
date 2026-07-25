"""Execution: submission shape, truncation, timeout-and-cancel, statement_id."""

from __future__ import annotations

import pytest
from databricks.sdk.service.sql import Disposition, StatementState
from portal.errors import ExecutionTimeout, PortalError
from portal.execution import choose_disposition, execute

from tests.conftest import FakeWorkspaceClient, response


def ok(**kwargs):
    kwargs.setdefault("columns", ["a"])
    kwargs.setdefault("rows", [["1"]])
    return response(state=StatementState.SUCCEEDED, **kwargs)


def run(client, settings, **overrides):
    params = {
        "statement": "SELECT 1",
        "warehouse_id": "wh-test",
        "max_rows": 100,
        "timeout_seconds": 30,
    }
    params.update(overrides)
    return execute(client, settings, **params)


# --------------------------------------------------------------------------- #
# Submission
# --------------------------------------------------------------------------- #


def test_submits_async_and_applies_row_limit(settings):
    client = FakeWorkspaceClient([ok()])

    run(client, settings, max_rows=500)

    call = client.statement_execution.calls[0]
    assert call["wait_timeout"] == "10s"
    assert call["on_wait_timeout"].value == "CONTINUE"
    assert call["row_limit"] == 500


def test_statement_id_is_captured(settings):
    client = FakeWorkspaceClient([ok(statement_id="stmt-abc")])
    assert run(client, settings).statement_id == "stmt-abc"


def test_statement_id_is_captured_even_on_failure(settings):
    """It is the chargeback join key, so a failed run must still record it."""
    client = FakeWorkspaceClient(
        [response(state=StatementState.FAILED, error="boom", statement_id="stmt-fail")]
    )

    with pytest.raises(PortalError) as exc:
        run(client, settings)

    assert "stmt-fail" in exc.value.technical


# --------------------------------------------------------------------------- #
# Disposition
# --------------------------------------------------------------------------- #


def test_small_results_use_inline(settings):
    assert choose_disposition(100, settings) is Disposition.INLINE


def test_large_results_use_external_links(settings):
    assert choose_disposition(settings.external_links_threshold + 1, settings) is (
        Disposition.EXTERNAL_LINKS
    )


# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #


def test_manifest_truncation_flag_is_trusted(settings):
    client = FakeWorkspaceClient([ok(rows=[["1"]], truncated=True, total_row_count=1)])
    assert run(client, settings).truncated is True


def test_hitting_the_row_limit_counts_as_truncated_without_a_flag(settings):
    """Older responses omit `truncated`; the boundary is the fallback signal."""
    rows = [[str(i)] for i in range(10)]
    client = FakeWorkspaceClient([ok(rows=rows, total_row_count=10)])

    assert run(client, settings, max_rows=10).truncated is True


def test_result_below_the_limit_is_not_truncated(settings):
    client = FakeWorkspaceClient([ok(rows=[["1"]], total_row_count=1)])
    assert run(client, settings, max_rows=10).truncated is False


def test_manifest_flag_wins_over_the_boundary_heuristic(settings):
    rows = [[str(i)] for i in range(10)]
    client = FakeWorkspaceClient([ok(rows=rows, truncated=False, total_row_count=10)])

    assert run(client, settings, max_rows=10).truncated is False


# --------------------------------------------------------------------------- #
# Polling, timeout and cancellation
# --------------------------------------------------------------------------- #


def test_polls_until_terminal(settings):
    client = FakeWorkspaceClient(
        [
            response(state=StatementState.PENDING),
            response(state=StatementState.RUNNING),
            ok(),
        ]
    )

    result = run(client, settings)

    assert result.row_count == 1
    assert client.statement_execution.polls, "a pending statement must be polled"


def test_timeout_cancels_the_statement(settings):
    """Honouring timeout_seconds means cancelling, not just giving up locally."""
    client = FakeWorkspaceClient([response(state=StatementState.RUNNING, statement_id="stmt-slow")])

    with pytest.raises(ExecutionTimeout):
        run(client, settings, timeout_seconds=0)

    assert client.statement_execution.cancelled == ["stmt-slow"]


def test_failed_statement_maps_to_portuguese_copy(settings):
    denied = "PERMISSION_DENIED: user does not have SELECT"
    client = FakeWorkspaceClient([response(state=StatementState.FAILED, error=denied)])

    with pytest.raises(PortalError) as exc:
        run(client, settings)

    assert "não tem acesso" in exc.value.user_message


def test_timeout_error_message_tells_the_user_what_to_do(settings):
    client = FakeWorkspaceClient([response(state=StatementState.RUNNING)])

    with pytest.raises(ExecutionTimeout) as exc:
        run(client, settings, timeout_seconds=0)

    assert "reduza o período" in exc.value.user_message.lower()
