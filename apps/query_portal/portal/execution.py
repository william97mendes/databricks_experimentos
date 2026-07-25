"""Asynchronous execution against the SQL Statement Execution API.

Always runs with the *user's* client — never the service principal. Submits with
`wait_timeout=10s` / `on_wait_timeout=CONTINUE`, then polls until the per-query
`timeout_seconds` elapses, cancelling the statement if it does.

`statement_id` is captured on every outcome, including failures: it is the join
key into `system.query.history` for bytes scanned and warehouse time, which is
how usage is charged back per requesting area.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    ExecuteStatementRequestOnWaitTimeout,
    Format,
    StatementParameterListItem,
    StatementState,
)

from portal.config import Settings
from portal.errors import ExecutionTimeout, PortalError, message_for_status

INITIAL_WAIT_TIMEOUT = "10s"
_TERMINAL_FAILURES = {StatementState.FAILED, StatementState.CLOSED}


@dataclass
class ExecutionResult:
    statement_id: str | None
    warehouse_id: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    disposition: str = Disposition.INLINE.value
    # Populated for EXTERNAL_LINKS results; chunks are streamed at download time
    # rather than materialized here.
    external_manifest: Any | None = None

    @property
    def is_external(self) -> bool:
        return self.disposition == Disposition.EXTERNAL_LINKS.value


def choose_disposition(max_rows: int, settings: Settings) -> Disposition:
    """INLINE for small results; EXTERNAL_LINKS once a result could be large.

    EXTERNAL_LINKS lets downloads stream chunk by chunk straight to the client
    instead of being buffered into a DataFrame first.
    """
    return (
        Disposition.EXTERNAL_LINKS
        if max_rows > settings.external_links_threshold
        else Disposition.INLINE
    )


def _state(response: Any) -> StatementState | None:
    status = getattr(response, "status", None)
    return getattr(status, "state", None)


def _error_message(response: Any) -> str | None:
    status = getattr(response, "status", None)
    error = getattr(status, "error", None)
    return getattr(error, "message", None)


def _extract_columns(response: Any) -> list[str]:
    manifest = getattr(response, "manifest", None)
    schema = getattr(manifest, "schema", None)
    columns = getattr(schema, "columns", None) or []
    return [c.name for c in columns]


def _is_truncated(response: Any, row_count: int, max_rows: int) -> bool:
    """Trust the manifest, and fall back to the row_limit boundary.

    `manifest.truncated` is set when the server applied `row_limit`. Older
    responses omit it, so hitting exactly `max_rows` is treated as truncated too.
    """
    manifest = getattr(response, "manifest", None)
    flagged = getattr(manifest, "truncated", None)
    if flagged is not None:
        return bool(flagged)
    return max_rows > 0 and row_count >= max_rows


def execute(
    user_client: WorkspaceClient,
    settings: Settings,
    *,
    statement: str,
    warehouse_id: str,
    parameters: Sequence[StatementParameterListItem] | None = None,
    max_rows: int,
    timeout_seconds: int,
) -> ExecutionResult:
    """Run a published query as the end user and return a typed result.

    Raises `ExecutionTimeout` after cancelling, or `PortalError` carrying mapped
    Portuguese copy when the statement fails.
    """
    disposition = choose_disposition(max_rows, settings)
    started = time.monotonic()

    response = user_client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        parameters=list(parameters) if parameters else None,
        row_limit=max_rows,
        disposition=disposition,
        format=Format.JSON_ARRAY,
        wait_timeout=INITIAL_WAIT_TIMEOUT,
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    statement_id = getattr(response, "statement_id", None)

    response = _poll(
        user_client,
        response,
        statement_id=statement_id,
        timeout_seconds=timeout_seconds,
        poll_interval=settings.poll_interval_seconds,
        started=started,
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    state = _state(response)

    if state in _TERMINAL_FAILURES:
        raise PortalError(
            message_for_status(_error_message(response)),
            technical=f"statement {statement_id} {state}: {_error_message(response)}",
        )
    if state == StatementState.CANCELED:
        raise ExecutionTimeout(technical=f"statement {statement_id} was cancelled")

    result = getattr(response, "result", None)
    rows = list(getattr(result, "data_array", None) or [])
    manifest = getattr(response, "manifest", None)
    total_rows = getattr(manifest, "total_row_count", None)
    row_count = int(total_rows) if total_rows is not None else len(rows)

    return ExecutionResult(
        statement_id=statement_id,
        warehouse_id=warehouse_id,
        columns=_extract_columns(response),
        rows=rows,
        row_count=row_count,
        truncated=_is_truncated(response, row_count, max_rows),
        duration_ms=duration_ms,
        disposition=disposition.value,
        external_manifest=manifest if disposition is Disposition.EXTERNAL_LINKS else None,
    )


def _poll(
    user_client: WorkspaceClient,
    response: Any,
    *,
    statement_id: str | None,
    timeout_seconds: int,
    poll_interval: float,
    started: float,
) -> Any:
    """Poll until terminal, cancelling once `timeout_seconds` is exceeded."""
    while _state(response) in (StatementState.PENDING, StatementState.RUNNING):
        if time.monotonic() - started >= timeout_seconds:
            _cancel(user_client, statement_id)
            raise ExecutionTimeout(
                technical=(
                    f"statement {statement_id} exceeded timeout_seconds={timeout_seconds}"
                )
            )
        time.sleep(poll_interval)
        response = user_client.statement_execution.get_statement(statement_id)
    return response


def _cancel(user_client: WorkspaceClient, statement_id: str | None) -> None:
    """Best effort: a failed cancel must not mask the timeout being reported."""
    if not statement_id:
        return
    try:
        user_client.statement_execution.cancel_execution(statement_id)
    except Exception:  # noqa: BLE001 - the timeout is the error that matters
        pass
