"""Error mapping must not turn a portal defect into bad advice for the user."""

from __future__ import annotations

import pytest
from databricks.sdk.service.sql import StatementState
from portal.errors import (
    MSG_INTERNAL,
    MSG_OBJECT_NOT_FOUND,
    MSG_PERMISSION_DENIED,
    MSG_TIMEOUT,
    MSG_WAREHOUSE_UNAVAILABLE,
    ExecutionTimeout,
    WarehouseUnavailable,
    message_for_status,
    to_user_message,
)
from portal.execution import execute

from tests.conftest import FakeWorkspaceClient, response


def run(client, settings, **overrides):
    params = {
        "statement": "SELECT 1",
        "warehouse_id": "wh-test",
        "max_rows": 100,
        "timeout_seconds": 0,
    }
    params.update(overrides)
    return execute(client, settings, **params)


# --------------------------------------------------------------------------- #
# The regression: kwarg names containing "timeout"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message",
    [
        "execute_statement() got an unexpected keyword argument 'on_wait_timeout'",
        "execute_statement() missing 1 required argument: 'wait_timeout'",
    ],
)
def test_a_typeerror_naming_our_timeout_kwargs_is_not_a_query_timeout(message):
    """`wait_timeout`/`on_wait_timeout` are passed on every call.

    A bare "timeout" substring match reported these as a slow query and told the
    user to shorten their period — advice that could never help.
    """
    assert to_user_message(TypeError(message)) == MSG_INTERNAL


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("bad signature"),
        AttributeError("'NoneType' object has no attribute 'state'"),
        NameError("name 'foo' is not defined"),
        ImportError("cannot import name 'Format'"),
    ],
)
def test_programming_errors_map_to_an_internal_error(exc):
    assert to_user_message(exc) == MSG_INTERNAL


# --------------------------------------------------------------------------- #
# Genuine conditions still map correctly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message",
    [
        "Statement timed out after 300s",
        "DEADLINE_EXCEEDED",
        "Query timeout reached",
        "The statement exceeded the time limit",
    ],
)
def test_real_timeouts_still_map_to_the_timeout_message(message):
    assert to_user_message(RuntimeError(message)) == MSG_TIMEOUT


def test_permission_errors_take_priority():
    assert to_user_message(RuntimeError("PERMISSION_DENIED: does not have SELECT")) == (
        MSG_PERMISSION_DENIED
    )


def test_missing_table_maps_to_object_not_found():
    assert to_user_message(RuntimeError("TABLE_OR_VIEW_NOT_FOUND: t")) == MSG_OBJECT_NOT_FOUND


def test_status_mapping_no_longer_fires_on_the_word_exceeded_alone():
    """"row limit exceeded" is not a timeout, and used to be reported as one."""
    assert message_for_status("The row limit was exceeded for this result") != MSG_TIMEOUT


def test_status_mapping_still_detects_a_real_timeout():
    assert message_for_status("Statement timed out") == MSG_TIMEOUT


# --------------------------------------------------------------------------- #
# Pending vs running: two different problems
# --------------------------------------------------------------------------- #


def test_timing_out_while_still_pending_blames_the_warehouse(settings):
    """The statement never started, so "reduza o período" would be wrong."""
    client = FakeWorkspaceClient([response(state=StatementState.PENDING, statement_id="s1")])

    with pytest.raises(WarehouseUnavailable) as exc:
        run(client, settings)

    assert exc.value.user_message == MSG_WAREHOUSE_UNAVAILABLE
    assert "reached_running=False" in exc.value.technical


def test_timing_out_after_running_blames_the_query(settings):
    client = FakeWorkspaceClient([response(state=StatementState.RUNNING, statement_id="s1")])

    with pytest.raises(ExecutionTimeout) as exc:
        run(client, settings)

    assert exc.value.user_message == MSG_TIMEOUT
    assert "reached_running=True" in exc.value.technical


def test_timeout_detail_records_elapsed_and_last_state(settings):
    """The audit row must explain the timeout without a repro."""
    client = FakeWorkspaceClient([response(state=StatementState.PENDING, statement_id="s1")])

    with pytest.raises(WarehouseUnavailable) as exc:
        run(client, settings)

    technical = exc.value.technical
    assert "s1" in technical
    assert "timeout_seconds=0" in technical
    assert "last state=" in technical


def test_a_pending_statement_is_still_cancelled(settings):
    client = FakeWorkspaceClient([response(state=StatementState.PENDING, statement_id="s1")])

    with pytest.raises(WarehouseUnavailable):
        run(client, settings)

    assert client.statement_execution.cancelled == ["s1"]
