from __future__ import annotations

from docpipe.models import DocType, Invoice, LineItem, LLMExtraction
from docpipe.validate import validate, validate_invoice


def make_invoice(**overrides) -> Invoice:
    base = dict(
        vendor="SparkleClean Supplies Ltd",
        invoice_number="INV-1",
        date="2026-09-01",
        due_date="2026-10-01",
        line_items=[LineItem(description="Cloths", quantity=2, unit_price=50, amount=100)],
        subtotal=100,
        tax=10,
        total=110,
        po_number="PO-1",
    )
    base.update(overrides)
    return Invoice(**base)


def codes(invoice: Invoice, doc_type: DocType = DocType.INVOICE) -> set[str]:
    return {i.code for i in validate_invoice(invoice, doc_type)}


def test_clean_invoice_has_no_issues():
    assert codes(make_invoice()) == set()


def test_totals_mismatch_is_flagged():
    assert "totals_mismatch" in codes(make_invoice(total=120))


def test_line_items_mismatch_is_flagged():
    assert "line_items_mismatch" in codes(make_invoice(subtotal=90, total=100))


def test_bad_dates_are_flagged():
    assert "bad_date" in codes(make_invoice(date="1st Sept"))
    assert "bad_due_date" in codes(make_invoice(due_date="soon"))
    assert "due_before_issue" in codes(make_invoice(due_date="2026-08-01"))


def test_missing_po_is_warning_for_invoices_only():
    issues = validate_invoice(make_invoice(po_number=None), DocType.INVOICE)
    assert [i.severity for i in issues if i.code == "missing_po"] == ["warning"]
    assert "missing_po" not in codes(make_invoice(po_number=None), DocType.RECEIPT)


def test_needs_review_rules():
    ok = LLMExtraction(doc_type=DocType.INVOICE, confidence=0.95, invoice=make_invoice())
    assert validate(ok) == ([], False)

    low_conf = LLMExtraction(doc_type=DocType.INVOICE, confidence=0.5, invoice=make_invoice())
    issues, review = validate(low_conf)
    assert review and {i.code for i in issues} == {"low_confidence"}

    no_fields = LLMExtraction(doc_type=DocType.INVOICE, confidence=0.9, invoice=None)
    issues, review = validate(no_fields)
    assert review and issues[0].code == "no_fields"

    other = LLMExtraction(doc_type=DocType.OTHER, confidence=0.9, invoice=None)
    assert validate(other) == ([], False)
