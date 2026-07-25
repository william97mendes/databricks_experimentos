"""Audit log writes.

These run as the *service principal*: business users are not granted access to
the metadata schema, and the log holds every user's activity. `user_email` is
taken from the authenticated identity, never from user input.

`statement_id` is recorded even on failure — it is the join key into
`system.query.history` for bytes scanned and warehouse time, which is how usage
is charged back per requesting area. (That system schema is unavailable on
Databricks Free Edition; the column is still populated so the join works the day
you move to a paid workspace.)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

from portal import sql
from portal.config import Settings

STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_CANCELED = "CANCELED"


def new_execution_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _p(name: str, value: Any, sql_type: str) -> StatementParameterListItem:
    text = None if value is None else str(value)
    return StatementParameterListItem(name=name, value=text, type=sql_type)


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    query_id: str
    user_email: str
    parameters: dict[str, Any]
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    statement_id: str | None = None
    warehouse_id: str | None = None
    error_message: str | None = None
    row_count: int | None = None
    duration_ms: int | None = None
    downloaded_format: str | None = None

    def to_parameters(self) -> list[StatementParameterListItem]:
        parameters_json = json.dumps(self.parameters, ensure_ascii=False, default=str)
        return [
            _p("execution_id", self.execution_id, "STRING"),
            _p("query_id", self.query_id, "STRING"),
            _p("user_email", self.user_email, "STRING"),
            _p("parameters", parameters_json, "STRING"),
            _p("statement_id", self.statement_id, "STRING"),
            _p("warehouse_id", self.warehouse_id, "STRING"),
            _p("status", self.status, "STRING"),
            # Technical detail only; the user sees mapped Portuguese copy.
            _p("error_message", self.error_message, "STRING"),
            _p("row_count", self.row_count, "BIGINT"),
            _p("duration_ms", self.duration_ms, "BIGINT"),
            _p("downloaded_format", self.downloaded_format, "STRING"),
            _p("started_at", _ts(self.started_at), "TIMESTAMP"),
            _p("ended_at", _ts(self.ended_at), "TIMESTAMP"),
        ]


class AuditLog:
    """Writes execution records with the service principal client."""

    def __init__(self, sp_client: WorkspaceClient, settings: Settings):
        self._client = sp_client
        self._settings = settings

    def record(self, execution: ExecutionRecord) -> None:
        """Persist one execution attempt.

        Auditing must never break the user's session, so failures here are
        swallowed after being surfaced to the app log.
        """
        try:
            self._client.statement_execution.execute_statement(
                statement=sql.insert_execution_log(self._settings),
                warehouse_id=self._settings.warehouse_id,
                parameters=execution.to_parameters(),
                wait_timeout="30s",
            )
        except Exception as exc:  # noqa: BLE001 - auditing is best effort
            print(f"[audit] failed to record execution {execution.execution_id}: {exc}")

    def record_download(self, execution_id: str, fmt: str) -> None:
        """Stamp the format the user actually downloaded."""
        try:
            self._client.statement_execution.execute_statement(
                statement=sql.update_downloaded_format(self._settings),
                warehouse_id=self._settings.warehouse_id,
                parameters=[
                    _p("downloaded_format", fmt, "STRING"),
                    _p("execution_id", execution_id, "STRING"),
                ],
                wait_timeout="30s",
            )
        except Exception as exc:  # noqa: BLE001 - auditing is best effort
            print(f"[audit] failed to record download for {execution_id}: {exc}")

    def recent_for_user(self, user_email: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Rows for "Minhas execuções".

        Consumer-access users cannot open Query History, so this tab is their
        only window into what they ran. The email filter is bound, and comes from
        the authenticated identity rather than anything the user typed.
        """
        from portal.metadata import rows_as_dicts

        response = self._client.statement_execution.execute_statement(
            statement=sql.select_user_executions(self._settings),
            warehouse_id=self._settings.warehouse_id,
            parameters=[
                _p("user_email", user_email, "STRING"),
                _p("row_limit", limit or self._settings.history_limit, "INT"),
            ],
            wait_timeout="30s",
        )
        return rows_as_dicts(response)


def started() -> tuple[str, datetime]:
    """Mint an execution id and start timestamp at the moment of submission."""
    return new_execution_id(), _utc_now()


def finished() -> datetime:
    return _utc_now()
