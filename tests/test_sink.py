from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from conftest import AS_OF, ROOT, make_settings

from docpipe import pipeline
from docpipe.sink import CsvSink, GoogleSheetsSink, build_sink


@pytest.fixture
def docs(tmp_path: Path, extractor):
    return pipeline.run(ROOT / "inbox", ROOT / "data", tmp_path / "o", extractor, AS_OF).processed


def test_build_sink_defaults_to_csv(tmp_path: Path, docs):
    sink = build_sink(make_settings(tmp_path))
    assert isinstance(sink, CsvSink)
    sink.write_exceptions(docs)
    assert sink.path.read_text().startswith("id,source_file,status,")


def test_sheets_sink_writes_rows_via_gspread(tmp_path: Path, docs, monkeypatch):
    calls: dict[str, object] = {}

    class Sheet:
        def clear(self):
            calls["cleared"] = True

        def update(self, cell, rows):
            calls["cell"], calls["rows"] = cell, rows

    class Book:
        def __init__(self):
            self.added: list[str] = []

        def worksheet(self, name):
            if name == "exceptions":
                return Sheet()
            raise fake.WorksheetNotFound

        def add_worksheet(self, title, rows, cols):
            self.added.append(title)
            return Sheet()

    book = Book()
    fake = ModuleType("gspread")
    fake.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})  # type: ignore[attr-defined]
    fake.service_account = lambda filename: SimpleNamespace(open_by_key=lambda key: book)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gspread", fake)

    creds = tmp_path / "sa.json"
    creds.write_text("{}")
    settings = make_settings(
        tmp_path, sink="sheets", sheets_spreadsheet_id="abc", google_credentials_file=creds
    )
    sink = build_sink(settings)
    assert isinstance(sink, GoogleSheetsSink)
    sink.write_exceptions(docs)
    assert calls["cleared"] and calls["cell"] == "A1"
    assert calls["rows"][0][0] == "id" and len(calls["rows"]) == 8  # header + 7 exceptions

    GoogleSheetsSink("abc", "new-tab", creds).write_exceptions(docs)
    assert book.added == ["new-tab"]
