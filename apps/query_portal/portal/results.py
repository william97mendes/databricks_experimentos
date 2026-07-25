"""Result serialization for downloads.

Rows are streamed: `iter_rows` yields row by row, pulling EXTERNAL_LINKS chunks
on demand, and the CSV/XLSX writers consume that iterator. No DataFrame is ever
materialized, so a large export costs one chunk of memory rather than the whole
result set.

CSV is encoded `utf-8-sig` — the BOM is what makes Excel open acentuação
(São Paulo, Ribeirão Preto) correctly instead of as mojibake.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from databricks.sdk import WorkspaceClient

from portal.execution import ExecutionResult

CSV_ENCODING = "utf-8-sig"
FORMAT_CSV = "CSV"
FORMAT_XLSX = "XLSX"

CONTENT_TYPES = {
    FORMAT_CSV: "text/csv",
    FORMAT_XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def filename(query_id: str, fmt: str, now: datetime | None = None) -> str:
    """`{query_id}_{YYYYMMDD_HHMM}.{ext}`."""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"{query_id}_{stamp}.{fmt.lower()}"


def iter_rows(
    result: ExecutionResult,
    user_client: WorkspaceClient | None = None,
) -> Iterator[list[Any]]:
    """Yield every row, fetching external chunks lazily as the caller consumes.

    INLINE results are already in memory. EXTERNAL_LINKS results are walked chunk
    by chunk, following `next_chunk_index` until the manifest is exhausted.
    """
    yield from result.rows

    if not result.is_external or user_client is None or not result.statement_id:
        return

    yield from _iter_external_chunks(user_client, result.statement_id)


def _iter_external_chunks(
    user_client: WorkspaceClient,
    statement_id: str,
) -> Iterator[list[Any]]:
    """Walk EXTERNAL_LINKS chunks, downloading one at a time."""
    import json
    import urllib.request

    chunk_index = 0
    while True:
        try:
            chunk = user_client.statement_execution.get_statement_result_chunk_n(
                statement_id, chunk_index
            )
        except Exception as exc:  # noqa: BLE001 - stop streaming, keep what we have
            print(f"[results] chunk {chunk_index} of {statement_id} failed: {exc}")
            return

        links = getattr(chunk, "external_links", None) or []
        if not links:
            return

        for link in links:
            url = getattr(link, "external_link", None)
            if not url:
                continue
            # Pre-signed URL: it already carries its own credentials, so no
            # Databricks auth header is attached here.
            with urllib.request.urlopen(url) as response:  # noqa: S310 - Databricks-issued URL
                payload = json.loads(response.read().decode("utf-8"))
            yield from payload

        next_index = getattr(links[-1], "next_chunk_index", None)
        if next_index is None:
            return
        chunk_index = next_index


def _cell(value: Any) -> Any:
    return "" if value is None else value


def to_csv_bytes(
    columns: list[str],
    rows: Iterator[list[Any]] | list[list[Any]],
) -> bytes:
    """Serialize to CSV with a BOM so Excel reads UTF-8 correctly."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return buffer.getvalue().encode(CSV_ENCODING)


def iter_csv_chunks(
    columns: list[str],
    rows: Iterator[list[Any]] | list[list[Any]],
    flush_every: int = 1_000,
) -> Iterator[bytes]:
    """Stream CSV in chunks, for exports too large to hold as one string.

    The first chunk carries the BOM, so concatenating the stream produces exactly
    what `to_csv_bytes` would.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(columns)

    first = True
    for index, row in enumerate(rows, start=1):
        writer.writerow([_cell(v) for v in row])
        if index % flush_every == 0:
            yield buffer.getvalue().encode(CSV_ENCODING if first else "utf-8")
            first = False
            buffer.seek(0)
            buffer.truncate(0)

    remaining = buffer.getvalue()
    if remaining or first:
        yield remaining.encode(CSV_ENCODING if first else "utf-8")


def to_xlsx_bytes(
    columns: list[str],
    rows: Iterator[list[Any]] | list[list[Any]],
    sheet_name: str = "Resultado",
) -> bytes:
    """Serialize to XLSX via xlsxwriter in constant-memory mode."""
    import xlsxwriter

    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        buffer,
        {"in_memory": True, "constant_memory": True, "default_date_format": "yyyy-mm-dd"},
    )
    # Excel caps sheet names at 31 chars and rejects several punctuation marks.
    worksheet = workbook.add_worksheet(sheet_name[:31])
    header = workbook.add_format({"bold": True})

    for column_index, name in enumerate(columns):
        worksheet.write(0, column_index, name, header)

    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row):
            worksheet.write(row_index, column_index, _cell(value))

    worksheet.freeze_panes(1, 0)
    workbook.close()
    return buffer.getvalue()


def serialize(
    fmt: str,
    columns: list[str],
    rows: Iterator[list[Any]] | list[list[Any]],
) -> bytes:
    if fmt == FORMAT_XLSX:
        return to_xlsx_bytes(columns, rows)
    return to_csv_bytes(columns, rows)
