"""Audit log: identity provenance, bound parameters, and failure tolerance."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from databricks.sdk.service.sql import StatementState
from portal.audit import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    AuditLog,
    ExecutionRecord,
    new_execution_id,
)

from tests.conftest import FakeWorkspaceClient, response

STARTED = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def record(**overrides) -> ExecutionRecord:
    base = {
        "execution_id": "exec-1",
        "query_id": "corridas_por_cep",
        "user_email": "ana@empresa.com.br",
        "parameters": {"periodo_inicio": "2016-01-01"},
        "status": STATUS_SUCCEEDED,
        "started_at": STARTED,
    }
    base.update(overrides)
    return ExecutionRecord(**base)


def ok_client():
    return FakeWorkspaceClient([response(state=StatementState.SUCCEEDED)])


def params_of(call) -> dict[str, str | None]:
    return {p.name: p.value for p in call["parameters"]}


def test_execution_ids_are_unique():
    assert new_execution_id() != new_execution_id()


def test_record_binds_every_value_as_a_parameter(settings):
    client = ok_client()

    AuditLog(client, settings).record(record())

    call = client.statement_execution.calls[0]
    values = params_of(call)
    assert values["execution_id"] == "exec-1"
    assert values["user_email"] == "ana@empresa.com.br"
    # The email must never be interpolated into the statement text.
    assert "ana@empresa.com.br" not in call["statement"]


def test_parameters_are_stored_as_json(settings):
    client = ok_client()

    AuditLog(client, settings).record(record(parameters={"cep": "10001", "x": None}))

    stored = json.loads(params_of(client.statement_execution.calls[0])["parameters"])
    assert stored == {"cep": "10001", "x": None}


def test_statement_id_is_persisted_for_chargeback(settings):
    client = ok_client()

    AuditLog(client, settings).record(record(statement_id="stmt-xyz"))

    assert params_of(client.statement_execution.calls[0])["statement_id"] == "stmt-xyz"


def test_failed_executions_are_recorded_with_technical_detail(settings):
    client = ok_client()

    AuditLog(client, settings).record(
        record(status=STATUS_FAILED, error_message="PERMISSION_DENIED on table x")
    )

    values = params_of(client.statement_execution.calls[0])
    assert values["status"] == STATUS_FAILED
    assert "PERMISSION_DENIED" in values["error_message"]


def test_timestamps_are_bound_as_timestamp_typed_parameters(settings):
    client = ok_client()

    AuditLog(client, settings).record(record())

    types = {p.name: p.type for p in client.statement_execution.calls[0]["parameters"]}
    assert types["started_at"] == "TIMESTAMP"
    assert types["row_count"] == "BIGINT"


def test_auditing_failure_never_breaks_the_user_session(settings):
    """A broken audit write must not lose the user their result."""

    class Exploding:
        def execute_statement(self, **_):
            raise RuntimeError("metastore unavailable")

    class Client:
        statement_execution = Exploding()

    AuditLog(Client(), settings).record(record())  # must not raise


def test_download_format_is_recorded(settings):
    client = ok_client()

    AuditLog(client, settings).record_download("exec-1", "XLSX")

    values = params_of(client.statement_execution.calls[0])
    assert values["downloaded_format"] == "XLSX"
    assert values["execution_id"] == "exec-1"


def test_history_filters_by_the_authenticated_email(settings):
    client = FakeWorkspaceClient(
        [
            response(
                state=StatementState.SUCCEEDED,
                columns=["execution_id", "query_id", "status"],
                rows=[["exec-1", "corridas_por_cep", "SUCCEEDED"]],
            )
        ]
    )

    rows = AuditLog(client, settings).recent_for_user("ana@empresa.com.br")

    call = client.statement_execution.calls[0]
    assert params_of(call)["user_email"] == "ana@empresa.com.br"
    assert "ana@empresa.com.br" not in call["statement"]
    assert rows[0]["query_id"] == "corridas_por_cep"


def test_history_row_limit_is_bound_not_interpolated(settings):
    client = FakeWorkspaceClient([response(state=StatementState.SUCCEEDED, columns=[], rows=[])])

    AuditLog(client, settings).recent_for_user("ana@empresa.com.br", limit=5)

    call = client.statement_execution.calls[0]
    assert params_of(call)["row_limit"] == "5"
    assert ":row_limit" in call["statement"]
