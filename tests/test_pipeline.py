from __future__ import annotations

import json
from pathlib import Path

from conftest import AS_OF, ROOT

from docpipe import pipeline
from docpipe.extract import load_document
from docpipe.models import DocType, ReconStatus
from docpipe.store import Store

EXPECTED_STATUS = {
    "inv_1001_sparkleclean.pdf": ReconStatus.PAID,
    "inv_1002_metro_janitorial.pdf": ReconStatus.UNPAID,
    "inv_1002_metro_janitorial_resend.pdf": ReconStatus.DUPLICATE,
    "inv_1003_brightway_uniforms.pdf": ReconStatus.OVERPAID,
    "inv_1005_northwind_equipment.pdf": ReconStatus.UNPAID,
    "inv_1007_acme_pest.pdf": ReconStatus.UNKNOWN_VENDOR,
    "inv_1008_sparkleclean.pdf": ReconStatus.PARTIALLY_PAID,
    "rcpt_2201_quickfuel.pdf": ReconStatus.PAID,
    "email_brightway_inv_1009.txt": ReconStatus.OVERDUE,
}


def test_pdf_and_email_text_extraction():
    pdf = load_document(ROOT / "inbox" / "inv_1001_sparkleclean.pdf")
    assert "INV-1001" in pdf.text and "PO-5001" in pdf.text and pdf.pdf_bytes
    txt = load_document(ROOT / "inbox" / "email_brightway_inv_1009.txt")
    assert "INV-1009" in txt.text and txt.pdf_bytes is None


def test_eml_parsing(tmp_path: Path):
    eml = tmp_path / "msg.eml"
    eml.write_bytes(
        b"From: a@example.com\r\nTo: b@example.com\r\nSubject: Invoice 42\r\n"
        b"Content-Type: text/plain\r\n\r\nTotal due 100.00\r\n"
    )
    doc = load_document(eml)
    assert "Subject: Invoice 42" in doc.text and "Total due 100.00" in doc.text


def test_full_run_on_sample_set(tmp_path: Path, extractor):
    out = tmp_path / "out"
    result = pipeline.run(ROOT / "inbox", ROOT / "data", out, extractor, as_of=AS_OF)
    assert len(result.processed) == 10 and result.skipped == []

    by_file = {d.source_file: d for d in result.processed}
    for name, status in EXPECTED_STATUS.items():
        assert by_file[name].reconciliation is not None, name
        assert by_file[name].reconciliation.status == status, name
    assert by_file["email_newsletter.txt"].doc_type == DocType.OTHER
    assert by_file["email_newsletter.txt"].reconciliation is None

    review = {d.source_file for d in result.processed if d.needs_review}
    assert review == {
        "inv_1002_metro_janitorial.pdf",
        "inv_1002_metro_janitorial_resend.pdf",
        "inv_1005_northwind_equipment.pdf",
    }
    assert {i.code for i in by_file["inv_1005_northwind_equipment.pdf"].issues} == {
        "totals_mismatch"
    }

    # Outputs exist and are well-formed.
    rows = [json.loads(line) for line in (out / "extracted.jsonl").read_text().splitlines()]
    assert len(rows) == 10
    csv_lines = (out / "exceptions.csv").read_text().splitlines()
    assert len(csv_lines) == 1 + 7  # header + 7 exceptions
    briefing = (out / "briefing.md").read_text()
    assert "Daily AP briefing - 2026-10-08" in briefing
    assert "Approval queue (3" in briefing
    assert result.briefing == briefing


def test_rerun_skips_processed_files(tmp_path: Path, extractor):
    out = tmp_path / "out"
    pipeline.run(ROOT / "inbox", ROOT / "data", out, extractor, as_of=AS_OF)
    second = pipeline.run(ROOT / "inbox", ROOT / "data", out, extractor, as_of=AS_OF)
    assert second.processed == [] and len(second.skipped) == 10

    store = Store(out / "docpipe.db")
    try:
        assert len(store.all_documents()) == 10
        assert "metro janitorial wholesale|inv-1002" in store.known_invoice_keys()
    finally:
        store.close()

    # Briefing can be regenerated from the store alone.
    text = pipeline.briefing_only(out, AS_OF)
    assert "Outstanding payable" in text
