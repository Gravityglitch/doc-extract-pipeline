"""Orchestration: ingest -> extract -> classify/extract -> validate -> reconcile -> outputs.

Failure isolation: each document is processed inside its own try/except. A document that
raises (corrupt PDF, model refusal, schema error, ...) is stored with status `failed` and
the error text, shows up in the exceptions list and the briefing, and the run continues.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from docpipe.config import Settings
from docpipe.extract import SourceDocument, file_sha256, list_inbox, load_document
from docpipe.llm import Extractor
from docpipe.logging_setup import bind_run, get_logger, new_run_id
from docpipe.models import DocStatus, DocType, ProcessedDocument
from docpipe.reconcile import Records, invoice_key, reconcile
from docpipe.report import write_briefing, write_jsonl
from docpipe.sink import CsvSink, Sink
from docpipe.store import Store
from docpipe.validate import validate

log = get_logger("docpipe.pipeline")


@dataclass
class RunResult:
    run_id: str
    processed: list[ProcessedDocument]
    skipped: list[str]
    briefing: str
    failed: list[ProcessedDocument] = field(default_factory=list)


def process_document(
    doc: SourceDocument,
    extractor: Extractor,
    records: Records,
    seen_keys: set[str],
    as_of: date,
    run_id: str | None = None,
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
        run_id=run_id,
        notes=extraction.notes,
    )


def failed_document(path: Path, sha256: str, exc: BaseException, as_of: date, run_id: str):
    return ProcessedDocument(
        source_file=path.name,
        sha256=sha256,
        extraction_method="none",
        doc_type=DocType.UNKNOWN,
        confidence=0.0,
        invoice=None,
        needs_review=True,
        processed_on=as_of,
        status=DocStatus.FAILED,
        error=f"{type(exc).__name__}: {exc}"[:2000],
        run_id=run_id,
    )


def run(
    inbox: Path,
    data_dir: Path,
    out_dir: Path,
    extractor: Extractor,
    as_of: date | None = None,
    store: Store | None = None,
    sink: Sink | None = None,
    run_id: str | None = None,
) -> RunResult:
    """One pass over `inbox`. `store` defaults to SQLite at out/docpipe.db; `sink`
    defaults to out/exceptions.csv."""
    as_of = as_of or date.today()
    run_id = run_id or new_run_id()
    bind_run(run_id)
    started = time.perf_counter()
    records = Records.load(data_dir)
    own_store = store is None
    store = store or Store(out_dir / "docpipe.db")
    sink = sink or CsvSink(out_dir / "exceptions.csv")
    seen_keys = store.known_invoice_keys()
    processed: list[ProcessedDocument] = []
    failed: list[ProcessedDocument] = []
    skipped: list[str] = []

    try:
        for path in list_inbox(inbox):
            sha = file_sha256(path.read_bytes())
            if store.is_processed(sha):
                log.debug("doc.skip", file=path.name, reason="already processed")
                skipped.append(path.name)
                continue
            log.info("doc.start", file=path.name)
            try:
                doc = load_document(path)
                result = process_document(doc, extractor, records, seen_keys, as_of, run_id)
            except Exception as exc:
                log.error("doc.failed", file=path.name, error=str(exc), exc_info=True)
                result = failed_document(path, sha, exc, as_of, run_id)
                failed.append(result)
            else:
                log.info(
                    "doc.done",
                    file=path.name,
                    doc_type=result.doc_type.value,
                    needs_review=result.needs_review,
                    recon=result.reconciliation.status.value if result.reconciliation else None,
                )
                processed.append(result)
            store.save(result)
        all_docs = store.all_documents()
    finally:
        if own_store:
            store.close()

    write_jsonl(all_docs, out_dir / "extracted.jsonl")
    sink.write_exceptions(all_docs)
    briefing = write_briefing(all_docs, out_dir / "briefing.md", as_of)
    usage = getattr(extractor, "usage", None)
    log.info(
        "run.done",
        processed=len(processed),
        failed=len(failed),
        skipped=len(skipped),
        seconds=round(time.perf_counter() - started, 2),
        llm_calls=getattr(usage, "calls", 0),
        llm_input_tokens=getattr(usage, "input_tokens", 0),
        llm_output_tokens=getattr(usage, "output_tokens", 0),
        llm_cost_usd=round(getattr(usage, "cost_usd", 0.0), 4),
        llm_retries=getattr(usage, "retries", 0),
    )
    return RunResult(
        run_id=run_id, processed=processed, skipped=skipped, briefing=briefing, failed=failed
    )


def run_with_settings(settings: Settings, extractor: Extractor, store: Store) -> RunResult:
    """`run()` wired from Settings: IMAP poll first when configured, then the folder."""
    from docpipe.ingest.imap import poll_from_settings  # local import: optional path
    from docpipe.sink import build_sink

    if settings.ingest_mode == "imap":
        poll_from_settings(settings)
    return run(
        settings.inbox_dir,
        settings.data_dir,
        settings.out_dir,
        extractor,
        as_of=settings.as_of,
        store=store,
        sink=build_sink(settings),
    )


def briefing_only(out_dir: Path, as_of: date | None = None, store: Store | None = None) -> str:
    own_store = store is None
    store = store or Store(out_dir / "docpipe.db")
    try:
        docs = store.all_documents()
    finally:
        if own_store:
            store.close()
    return write_briefing(docs, out_dir / "briefing.md", as_of or date.today())
