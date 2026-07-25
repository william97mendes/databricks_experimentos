"""Shared fixtures. The Statement Execution API is always mocked in unit tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from portal.config import Settings
from portal.metadata import QueryDefinition, QueryParameter
from portal.params import ParamType


@pytest.fixture
def settings() -> Settings:
    return Settings(
        catalog="governance",
        schema="portal",
        warehouse_id="wh-test",
        timezone="America/Sao_Paulo",
        external_links_threshold=1_000,
        default_max_rows=10_000,
        default_timeout_seconds=60,
        poll_interval_seconds=0.0,
    )


def make_param(
    name: str = "periodo",
    param_type: ParamType = ParamType.DATE_RANGE,
    **overrides: Any,
) -> QueryParameter:
    base = {
        "query_id": "q1",
        "param_name": name,
        "label": overrides.pop("label", name),
        "param_type": param_type,
    }
    base.update(overrides)
    return QueryParameter(**base)


def make_query(**overrides: Any) -> QueryDefinition:
    base = {
        "query_id": "q1",
        "title": "Consulta de teste",
        "sql_template": "SELECT 1",
    }
    base.update(overrides)
    return QueryDefinition(**base)


# --------------------------------------------------------------------------- #
# Fakes for the Statement Execution API
# --------------------------------------------------------------------------- #


@dataclass
class FakeColumn:
    name: str


@dataclass
class FakeSchema:
    columns: list[FakeColumn]


@dataclass
class FakeManifest:
    schema: FakeSchema
    truncated: bool | None = None
    total_row_count: int | None = None


@dataclass
class FakeResultData:
    data_array: list[list[Any]]


@dataclass
class FakeError:
    message: str


@dataclass
class FakeStatus:
    state: Any
    error: FakeError | None = None


@dataclass
class FakeResponse:
    statement_id: str = "stmt-1"
    status: FakeStatus | None = None
    manifest: FakeManifest | None = None
    result: FakeResultData | None = None


def response(
    *,
    state: Any,
    columns: list[str] | None = None,
    rows: list[list[Any]] | None = None,
    truncated: bool | None = None,
    total_row_count: int | None = None,
    error: str | None = None,
    statement_id: str = "stmt-1",
) -> FakeResponse:
    """Build a StatementResponse-shaped object with only the fields we read."""
    manifest = None
    if columns is not None or truncated is not None or total_row_count is not None:
        manifest = FakeManifest(
            schema=FakeSchema([FakeColumn(c) for c in (columns or [])]),
            truncated=truncated,
            total_row_count=total_row_count,
        )
    return FakeResponse(
        statement_id=statement_id,
        status=FakeStatus(state=state, error=FakeError(error) if error else None),
        manifest=manifest,
        result=FakeResultData(rows) if rows is not None else None,
    )


class FakeStatementExecution:
    """Records calls and replays a scripted sequence of responses."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.polls: list[str] = []

    def execute_statement(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return self._next()

    def get_statement(self, statement_id: str) -> FakeResponse:
        self.polls.append(statement_id)
        return self._next()

    def cancel_execution(self, statement_id: str) -> None:
        self.cancelled.append(statement_id)

    def _next(self) -> FakeResponse:
        if not self._responses:
            raise AssertionError("FakeStatementExecution ran out of scripted responses")
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


class FakeWorkspaceClient:
    def __init__(self, responses: list[FakeResponse]):
        self.statement_execution = FakeStatementExecution(responses)
