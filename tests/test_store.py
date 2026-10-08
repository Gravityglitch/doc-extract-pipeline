from __future__ import annotations

from pathlib import Path

from conftest import AS_OF, ROOT

from docpipe import pipeline
from docpipe.models import DocStatus, DocType, Invoice, ProcessedDocument
from docpipe.store import Store


def _doc(name: str, sha: str, status: DocStatus = DocStatus.PROCESSED) -> ProcessedDocument:
    return ProcessedDocument(
        source_file=name,
        sha256=sha,
        extraction_method="fixture",
        doc_type=DocType.INVOICE,
        confidence=0.9,
        invoice=Invoice(
            vendor="Acme", invoice_number="INV-1", date="2026-01-01", subtotal=10, total=10
        ),
        processed_on=AS_OF,
        status=status,
    )


def test_save_roundtrip_and_ids(tmp_path: Path):
    store = Store(tmp_path / "s.db")
    saved = store.save(_doc("a.pdf", "a" * 64))
    assert saved.id is not None
    got = store.get(saved.id)
    assert got == saved and got.invoice and got.invoice.vendor == "Acme"
    assert store.get(9999) is None and store.get_by_sha("a" * 64) is not None
    store.close()


def test_dedupe_persists_across_runs_and_store_instances(tmp_path: Path, extractor):
    out = tmp_path / "out"
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    first = Store(url)
    r1 = pipeline.run(ROOT / "inbox", ROOT / "data", out, extractor, as_of=AS_OF, store=first)
    first.close()
    assert len(r1.processed) == 10

    second = Store(url)
    r2 = pipeline.run(ROOT / "inbox", ROOT / "data", out, extractor, as_of=AS_OF, store=second)
    assert r2.processed == [] and len(r2.skipped) == 10
    assert len(second.all_documents()) == 10
    assert all(d.run_id == r1.run_id for d in second.all_documents())
    second.close()


def test_filters_and_review_decisions(tmp_path: Path, extractor):
    store = Store(tmp_path / "s.db")
    pipeline.run(ROOT / "inbox", ROOT / "data", tmp_path / "out", extractor, AS_OF, store=store)
    review = store.all_documents(needs_review=True)
    assert {d.source_file for d in review} == {
        "inv_1002_metro_janitorial.pdf",
        "inv_1002_metro_janitorial_resend.pdf",
        "inv_1005_northwind_equipment.pdf",
    }
    target = next(d for d in review if d.source_file == "inv_1005_northwind_equipment.pdf")
    assert target.id is not None
    updated = store.record_decision(target.id, "approved", "vikrant", "checked with vendor")
    assert updated and updated.status == DocStatus.APPROVED and not updated.needs_review
    assert updated.decisions[0].reviewer == "vikrant"
    assert len(store.all_documents(status=DocStatus.APPROVED)) == 1
    assert len(store.all_documents(needs_review=True)) == 2
    assert store.record_decision(9999, "approved", "x") is None
    store.close()


def test_known_keys_exclude_duplicates_failed_and_rejected(tmp_path: Path):
    store = Store(tmp_path / "s.db")
    store.save(_doc("a.pdf", "a" * 64))
    store.save(_doc("b.pdf", "b" * 64, DocStatus.FAILED))
    rejected = store.save(_doc("c.pdf", "c" * 64))
    assert rejected.id is not None
    store.record_decision(rejected.id, "rejected", "me")
    assert store.known_invoice_keys() == {"acme|inv-1"}
    store.close()


def test_forget_and_reset(tmp_path: Path):
    store = Store(tmp_path / "s.db")
    store.save(_doc("a.pdf", "a" * 64))
    store.save(_doc("b.pdf", "b" * 64))
    assert store.forget(source_file="a.pdf") == 1
    assert not store.is_processed("a" * 64) and store.is_processed("b" * 64)
    assert store.forget(sha256="b" * 64) == 1
    store.save(_doc("a.pdf", "a" * 64))
    assert store.reset() == 1 and store.all_documents() == []
    assert store.ping()
    store.close()
