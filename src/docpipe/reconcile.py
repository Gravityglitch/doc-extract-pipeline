"""Match extracted invoices against the payments ledger and purchase orders."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from docpipe.models import (
    DocType,
    Invoice,
    LedgerEntry,
    PurchaseOrder,
    Reconciliation,
    ReconStatus,
)

AMOUNT_TOLERANCE = 0.01


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(",", " ").replace(".", " ").split())


def _norm_vendor(s: str) -> str:
    """Loose vendor matching: lowercase, strip punctuation and legal suffixes."""
    tokens = [t for t in _norm(s).split() if t not in {"ltd", "llc", "inc", "co", "limited"}]
    return " ".join(tokens)


@dataclass
class Records:
    ledger: list[LedgerEntry]
    purchase_orders: list[PurchaseOrder]
    known_vendors: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.known_vendors = {_norm_vendor(p.vendor) for p in self.purchase_orders} | {
            _norm_vendor(e.vendor) for e in self.ledger
        }

    @classmethod
    def load(cls, data_dir: Path) -> Records:
        with (data_dir / "ledger.csv").open(newline="") as f:
            ledger = [LedgerEntry.model_validate(r) for r in csv.DictReader(f)]
        with (data_dir / "purchase_orders.csv").open(newline="") as f:
            pos = [PurchaseOrder.model_validate(r) for r in csv.DictReader(f)]
        return cls(ledger, pos)

    def payments_for(self, inv: Invoice) -> list[LedgerEntry]:
        return [
            e
            for e in self.ledger
            if _norm(e.invoice_number) == _norm(inv.invoice_number)
            and _norm_vendor(e.vendor) == _norm_vendor(inv.vendor)
        ]

    def po_for(self, po_number: str | None) -> PurchaseOrder | None:
        if not po_number:
            return None
        return next(
            (p for p in self.purchase_orders if _norm(p.po_number) == _norm(po_number)), None
        )


def invoice_key(inv: Invoice) -> str:
    return f"{_norm_vendor(inv.vendor)}|{_norm(inv.invoice_number)}"


def reconcile(
    inv: Invoice,
    doc_type: DocType,
    records: Records,
    seen_keys: set[str],
    as_of: date,
) -> Reconciliation:
    """`seen_keys` holds invoice keys already processed (this run + SQLite store) so a
    re-sent invoice is flagged as a duplicate rather than paid twice."""
    key = invoice_key(inv)
    if key in seen_keys:
        return Reconciliation(
            status=ReconStatus.DUPLICATE,
            detail=f"{inv.invoice_number} from {inv.vendor} was already processed",
        )

    po = records.po_for(inv.po_number)
    payments = records.payments_for(inv)
    paid = round(sum(p.amount for p in payments), 2)
    base = {
        "paid_amount": paid,
        "po_found": po is not None,
        "po_amount": po.amount if po else None,
    }

    if _norm_vendor(inv.vendor) not in records.known_vendors:
        return Reconciliation(
            status=ReconStatus.UNKNOWN_VENDOR,
            detail=f"{inv.vendor!r} not in purchase orders or ledger",
            **base,
        )

    if doc_type == DocType.RECEIPT:
        status = ReconStatus.PAID if paid > 0 else ReconStatus.UNPAID
        detail = "Receipt matched to card/cash payment" if paid else "Receipt has no ledger entry"
        return Reconciliation(status=status, detail=detail, **base)

    po_note = (
        f"; PO {po.po_number} is for {po.amount:.2f}"
        if po and abs(po.amount - inv.total) > AMOUNT_TOLERANCE
        else ""
    )
    diff = round(paid - inv.total, 2)
    if abs(diff) <= AMOUNT_TOLERANCE:
        return Reconciliation(status=ReconStatus.PAID, detail=f"Paid in full{po_note}", **base)
    if diff > 0:
        return Reconciliation(
            status=ReconStatus.OVERPAID, detail=f"Overpaid by {diff:.2f}{po_note}", **base
        )
    if paid > 0:
        return Reconciliation(
            status=ReconStatus.PARTIALLY_PAID,
            detail=f"Outstanding {-diff:.2f} of {inv.total:.2f}{po_note}",
            **base,
        )
    due = _safe_date(inv.due_date)
    if due is not None and due < as_of:
        return Reconciliation(
            status=ReconStatus.OVERDUE,
            detail=f"Unpaid, due {due.isoformat()} ({(as_of - due).days} days ago){po_note}",
            **base,
        )
    return Reconciliation(status=ReconStatus.UNPAID, detail=f"No payment found{po_note}", **base)


def _safe_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
