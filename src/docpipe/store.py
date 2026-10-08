"""SQLite store: processed-file dedupe plus the full result for each document."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from docpipe.models import ProcessedDocument
from docpipe.reconcile import invoice_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    processed_on TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    invoice_key TEXT,
    recon_status TEXT,
    needs_review INTEGER NOT NULL,
    payload TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def is_processed(self, sha256: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
        return row is not None

    def known_invoice_keys(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT invoice_key FROM documents WHERE invoice_key IS NOT NULL "
            "AND recon_status != 'duplicate'"
        )
        return {r[0] for r in rows}

    def save(self, doc: ProcessedDocument) -> None:
        key = invoice_key(doc.invoice) if doc.invoice else None
        self.conn.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc.sha256,
                doc.source_file,
                doc.processed_on.isoformat(),
                doc.doc_type.value,
                key,
                doc.reconciliation.status.value if doc.reconciliation else None,
                int(doc.needs_review),
                doc.model_dump_json(),
            ),
        )
        self.conn.commit()

    def all_documents(self) -> list[ProcessedDocument]:
        rows = self.conn.execute("SELECT payload FROM documents ORDER BY source_file")
        return [ProcessedDocument.model_validate_json(r[0]) for r in rows]
