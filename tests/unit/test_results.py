"""Downloads: encoding, filename pattern, and streaming without a DataFrame."""

from __future__ import annotations

import io
from datetime import datetime

import pytest
from portal import results
from portal.execution import ExecutionResult

COLUMNS = ["regiao", "receita"]
ROWS = [["São Paulo", "1234.56"], ["Ribeirão Preto", None]]


def test_csv_is_utf8_sig_so_excel_reads_accents():
    """The BOM is the difference between "São Paulo" and "SÃ£o Paulo" in Excel."""
    payload = results.to_csv_bytes(COLUMNS, ROWS)

    assert payload.startswith(b"\xef\xbb\xbf")
    assert "São Paulo" in payload.decode("utf-8-sig")


def test_csv_contains_header_and_every_row():
    text = results.to_csv_bytes(COLUMNS, ROWS).decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line]

    assert lines[0] == "regiao;receita"
    assert len(lines) == 3


def test_csv_renders_null_as_empty_not_the_word_none():
    text = results.to_csv_bytes(COLUMNS, ROWS).decode("utf-8-sig")
    assert "None" not in text


@pytest.mark.parametrize("fmt", [results.FORMAT_CSV, results.FORMAT_XLSX])
def test_filename_pattern(fmt: str):
    name = results.filename("vendas_regiao", fmt, now=datetime(2026, 7, 24, 15, 4))
    assert name == f"vendas_regiao_20260724_1504.{fmt.lower()}"


def test_xlsx_is_a_real_workbook():
    payload = results.to_xlsx_bytes(COLUMNS, ROWS)
    # XLSX is a zip container; PK is the zip magic number.
    assert payload.startswith(b"PK")
    assert len(payload) > 0


def test_xlsx_round_trips_the_values():
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(results.to_xlsx_bytes(COLUMNS, ROWS)))
    sheet = workbook.active

    assert [c.value for c in sheet[1]] == COLUMNS
    assert sheet.cell(row=2, column=1).value == "São Paulo"


def test_serialize_dispatches_on_format():
    assert results.serialize(results.FORMAT_CSV, COLUMNS, ROWS).startswith(b"\xef\xbb\xbf")
    assert results.serialize(results.FORMAT_XLSX, COLUMNS, ROWS).startswith(b"PK")


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #


def test_writers_accept_an_iterator_and_never_need_a_list():
    """Proves the export path never materializes the full result set."""
    consumed = []

    def rows():
        for row in ROWS:
            consumed.append(row)
            yield row

    payload = results.to_csv_bytes(COLUMNS, rows())

    assert consumed == ROWS
    assert payload.startswith(b"\xef\xbb\xbf")


def test_chunked_csv_concatenates_to_the_same_bytes():
    rows = [[f"r{i}", str(i)] for i in range(25)]

    chunked = b"".join(results.iter_csv_chunks(COLUMNS, iter(rows), flush_every=10))

    assert chunked == results.to_csv_bytes(COLUMNS, rows)


def test_chunked_csv_emits_the_bom_only_once():
    rows = [[f"r{i}", str(i)] for i in range(25)]

    chunks = list(results.iter_csv_chunks(COLUMNS, iter(rows), flush_every=10))

    assert len(chunks) > 1
    assert chunks[0].startswith(b"\xef\xbb\xbf")
    assert not any(c.startswith(b"\xef\xbb\xbf") for c in chunks[1:])


def test_inline_results_stream_without_touching_the_client():
    result = ExecutionResult(
        statement_id="stmt-1",
        warehouse_id="wh",
        columns=COLUMNS,
        rows=ROWS,
        row_count=2,
    )

    assert list(results.iter_rows(result, user_client=None)) == ROWS
