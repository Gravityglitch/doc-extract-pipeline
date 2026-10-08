"""Exceptions sink: where the approval queue goes for humans.

`Sink` is a Protocol so the destination is swappable. The CSV adapter is the default and
has no external dependencies; the Google Sheets adapter needs the `sheets` extra
(`uv sync --extra sheets`) and a service-account JSON file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from docpipe.config import Settings
from docpipe.models import ProcessedDocument
from docpipe.report import exception_rows, write_exceptions_csv


class Sink(Protocol):
    def write_exceptions(self, docs: list[ProcessedDocument]) -> None: ...


class CsvSink:
    def __init__(self, path: Path):
        self.path = path

    def write_exceptions(self, docs: list[ProcessedDocument]) -> None:
        write_exceptions_csv(docs, self.path)


class GoogleSheetsSink:
    """Replaces the contents of one worksheet with the current exceptions table."""

    def __init__(self, spreadsheet_id: str, worksheet: str, credentials_file: Path):
        self.spreadsheet_id = spreadsheet_id
        self.worksheet = worksheet
        self.credentials_file = credentials_file

    def _open(self):  # noqa: ANN202 - gspread types are optional at import time
        import gspread  # local import: optional dependency

        client = gspread.service_account(filename=str(self.credentials_file))
        book = client.open_by_key(self.spreadsheet_id)
        try:
            return book.worksheet(self.worksheet)
        except gspread.WorksheetNotFound:
            return book.add_worksheet(title=self.worksheet, rows=1000, cols=10)

    def write_exceptions(self, docs: list[ProcessedDocument]) -> None:
        sheet = self._open()
        sheet.clear()
        sheet.update("A1", exception_rows(docs))


def build_sink(settings: Settings) -> Sink:
    if settings.sink == "sheets":
        assert settings.sheets_spreadsheet_id and settings.google_credentials_file
        return GoogleSheetsSink(
            settings.sheets_spreadsheet_id,
            settings.sheets_worksheet,
            settings.google_credentials_file,
        )
    return CsvSink(settings.out_dir / "exceptions.csv")
