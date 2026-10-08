"""Orchestration: inbox -> extract -> classify/extract -> validate -> reconcile -> outputs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from docpipe.extract import SourceDocument, list_inbox, load_document
from docpipe.llm import Extractor
from docpipe.models import ProcessedDocument
from docpipe.reconcile import Records, invoice_key, reconcile
from docpipe.report import write_briefing, write_exceptions_csv, write_jsonl
from docpipe.store import Store
from docpipe.validate import validate

log = logging.getLogger("docpipe")


@dataclass
class RunResult:
    processed: list[ProcessedDocument]
    skipped: list[str]
    briefing: str


def process_document(
    doc: SourceDocument,
    extractor: Extractor,
    records: Records,
    seen_keys: set[str],
    as_of: date,
) -> ProcessedDocument:
    extraction, method = extractor.extract(doc)
    issues, needs_review = validate(extraction)
    recon = None
    if extraction.invoice is not None:
        recon = reconcile(extraction.invoice, extraction.doc_type, records, seen_keys, as_of)
        seen_keys.add(invoice_key(extraction.invoice))
    return ProcessedDocument(
        source_file=doc.path.name,
        sha256=doc.sha256,
        extraction_method=method,
        doc_type=extraction.doc_type,
        confidence=extraction.confidence,
        invoice=extraction.invoice,
        issues=issues,
        needs_review=needs_review,
        reconciliation=recon,
        processed_on=as_of,
    )


def run(
    inbox: Path,
    data_dir: Path,
    out_dir: Path,
    extractor: Extractor,
    as_of: date | None = None,
) -> RunResult:
    as_of = as_of or date.today()
    records = Records.load(data_dir)
    store = Store(out_dir / "docpipe.db")
    seen_keys = store.known_invoice_keys()
    processed: list[ProcessedDocument] = []
    skipped: list[str] = []

    try:
        for path in list_inbox(inbox):
            doc = load_document(path)
            if store.is_processed(doc.sha256):
                log.info("skip (already processed): %s", path.name)
                skipped.append(path.name)
                continue
            log.info("processing %s", path.name)
            result = process_document(doc, extractor, records, seen_keys, as_of)
            store.save(result)
            processed.append(result)
        all_docs = store.all_documents()
    finally:
        store.close()

    write_jsonl(all_docs, out_dir / "extracted.jsonl")
    write_exceptions_csv(all_docs, out_dir / "exceptions.csv")
    briefing = write_briefing(all_docs, out_dir / "briefing.md", as_of)
    return RunResult(processed=processed, skipped=skipped, briefing=briefing)


def briefing_only(out_dir: Path, as_of: date | None = None) -> str:
    store = Store(out_dir / "docpipe.db")
    try:
        docs = store.all_documents()
    finally:
        store.close()
    return write_briefing(docs, out_dir / "briefing.md", as_of or date.today())
