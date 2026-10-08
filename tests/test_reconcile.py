from __future__ import annotations

from datetime import date

from conftest import AS_OF

from docpipe.models import DocType, Invoice, ReconStatus
from docpipe.reconcile import Records, invoice_key, reconcile


def inv(vendor: str, number: str, total: float, po: str | None = None, due="2026-10-30") -> Invoice:
    return Invoice(
        vendor=vendor,
        invoice_number=number,
        date="2026-09-01",
        due_date=due,
        subtotal=total,
        tax=0,
        total=total,
        po_number=po,
    )


def recon(
    records: Records, invoice: Invoice, seen: set[str] | None = None, doc_type=DocType.INVOICE
):
    return reconcile(invoice, doc_type, records, seen if seen is not None else set(), AS_OF)


def test_paid_in_full(records):
    r = recon(records, inv("SparkleClean Supplies Ltd", "INV-1001", 1100, "PO-5001"))
    assert r.status == ReconStatus.PAID
    assert r.paid_amount == 1100 and r.po_found and r.po_amount == 1100


def test_vendor_matching_is_loose(records):
    r = recon(records, inv("sparkleclean supplies", "inv-1001", 1100))
    assert r.status == ReconStatus.PAID


def test_overpaid(records):
    r = recon(records, inv("Brightway Uniforms", "INV-1003", 480, "PO-5003"))
    assert r.status == ReconStatus.OVERPAID
    assert "60.00" in r.detail


def test_partially_paid(records):
    r = recon(records, inv("SparkleClean Supplies Ltd", "INV-1008", 1650, "PO-5008"))
    assert r.status == ReconStatus.PARTIALLY_PAID
    assert r.paid_amount == 1000


def test_unpaid_and_overdue(records):
    unpaid = recon(records, inv("Metro Janitorial Wholesale", "INV-1002", 462))
    assert unpaid.status == ReconStatus.UNPAID
    overdue = recon(
        records, inv("Brightway Uniforms", "INV-1009", 360, "PO-5009", due="2026-09-19")
    )
    assert overdue.status == ReconStatus.OVERDUE
    assert "19 days" in overdue.detail


def test_unknown_vendor(records):
    r = recon(records, inv("Acme Pest Control", "INV-1007", 385, "PO-9999"))
    assert r.status == ReconStatus.UNKNOWN_VENDOR
    assert not r.po_found


def test_duplicate_detection(records):
    first = inv("Metro Janitorial Wholesale", "INV-1002", 462)
    seen = {invoice_key(first)}
    r = recon(records, inv("Metro Janitorial Wholesale", "INV-1002", 462), seen)
    assert r.status == ReconStatus.DUPLICATE


def test_po_amount_mismatch_is_mentioned(records):
    r = recon(records, inv("Northwind Equipment Hire", "INV-1005", 1180, "PO-5005"))
    assert r.status == ReconStatus.UNPAID
    assert "PO-5005 is for 1080.00" in r.detail


def test_receipt_matches_card_payment(records):
    r = recon(
        records, inv("QuickFuel Station", "RCPT-2201", 74.71, due=None), doc_type=DocType.RECEIPT
    )
    assert r.status == ReconStatus.PAID


def test_as_of_controls_overdue(records):
    early = reconcile(
        inv("Brightway Uniforms", "INV-1009", 360, due="2026-09-19"),
        DocType.INVOICE,
        records,
        set(),
        date(2026, 9, 1),
    )
    assert early.status == ReconStatus.UNPAID
