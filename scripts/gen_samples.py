"""Generate the fictional sample set: inbox PDFs/emails, ledger + PO CSVs, and the
ground-truth fixture used by tests and `docpipe run --mock`.

Run: uv run python scripts/gen_samples.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
DATA = ROOT / "data"
FIXTURES = ROOT / "tests" / "fixtures" / "extractions.json"

COMPANY = "Gleam & Go Commercial Cleaning\n14 Harbour Lane\nSpringfield, 90210"


def item(desc: str, qty: float, unit: float) -> dict[str, Any]:
    return {
        "description": desc,
        "quantity": qty,
        "unit_price": unit,
        "amount": round(qty * unit, 2),
    }


def invoice(
    vendor: str,
    number: str,
    date: str,
    due: str | None,
    items: list[dict[str, Any]],
    tax_rate: float,
    po: str | None,
    total_override: float | None = None,
) -> dict[str, Any]:
    subtotal = round(sum(i["amount"] for i in items), 2)
    tax = round(subtotal * tax_rate, 2)
    return {
        "vendor": vendor,
        "invoice_number": number,
        "date": date,
        "due_date": due,
        "line_items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total_override if total_override is not None else round(subtotal + tax, 2),
        "currency": "USD",
        "po_number": po,
    }


# One spec drives the PDFs, the emails and the ground-truth fixture.
SAMPLES: list[dict[str, Any]] = [
    {
        "file": "inv_1001_sparkleclean.pdf",
        "doc_type": "invoice",
        "confidence": 0.97,
        "invoice": invoice(
            "SparkleClean Supplies Ltd",
            "INV-1001",
            "2026-09-10",
            "2026-10-10",
            [
                item("Microfibre cloths (pack of 10)", 50, 4.00),
                item("Floor cleaner concentrate 5L", 20, 25.00),
                item("Heavy-duty bin liners (roll)", 10, 30.00),
            ],
            0.10,
            "PO-5001",
        ),
        "note": "Clean invoice, paid in full.",
    },
    {
        "file": "inv_1002_metro_janitorial.pdf",
        "doc_type": "invoice",
        "confidence": 0.93,
        "invoice": invoice(
            "Metro Janitorial Wholesale",
            "INV-1002",
            "2026-09-25",
            "2026-10-25",
            [
                item("Industrial vacuum bags (box)", 4, 45.00),
                item("Commercial mop system", 2, 120.00),
            ],
            0.10,
            None,
        ),
        "note": "EDGE CASE: no PO number printed.",
    },
    {
        "file": "inv_1003_brightway_uniforms.pdf",
        "doc_type": "invoice",
        "confidence": 0.96,
        "invoice": invoice(
            "Brightway Uniforms",
            "INV-1003",
            "2026-09-15",
            "2026-10-15",
            [item("Embroidered polo shirts", 12, 40.00)],
            0.0,
            "PO-5003",
        ),
        "note": "EDGE CASE: ledger shows 540.00 paid against a 480.00 invoice (overpaid).",
    },
    {
        "file": "inv_1002_metro_janitorial_resend.pdf",
        "doc_type": "invoice",
        "confidence": 0.92,
        "invoice": invoice(
            "Metro Janitorial Wholesale",
            "INV-1002",
            "2026-09-25",
            "2026-10-25",
            [
                item("Industrial vacuum bags (box)", 4, 45.00),
                item("Commercial mop system", 2, 120.00),
            ],
            0.10,
            None,
        ),
        "banner": "REMINDER - COPY OF ORIGINAL INVOICE",
        "note": "EDGE CASE: duplicate of INV-1002 re-sent as a reminder.",
    },
    {
        "file": "inv_1005_northwind_equipment.pdf",
        "doc_type": "invoice",
        "confidence": 0.90,
        "invoice": invoice(
            "Northwind Equipment Hire",
            "INV-1005",
            "2026-09-28",
            "2026-10-12",
            [item("Scrubber-dryer hire (2 weeks)", 1, 900.00)],
            0.20,
            "PO-5005",
            total_override=1180.00,
        ),
        "note": "EDGE CASE: printed total 1180.00 but 900 + 180 tax = 1080 (amount mismatch).",
    },
    {
        "file": "rcpt_2201_quickfuel.pdf",
        "doc_type": "receipt",
        "confidence": 0.95,
        "invoice": invoice(
            "QuickFuel Station",
            "RCPT-2201",
            "2026-10-02",
            None,
            [item("Diesel 48.2 L", 1, 74.71)],
            0.0,
            None,
        ),
        "receipt": True,
        "note": "Card receipt, matched to ledger card payment.",
    },
    {
        "file": "inv_1007_acme_pest.pdf",
        "doc_type": "invoice",
        "confidence": 0.94,
        "invoice": invoice(
            "Acme Pest Control",
            "INV-1007",
            "2026-09-30",
            "2026-10-30",
            [item("Quarterly pest treatment - 3 sites", 1, 350.00)],
            0.10,
            "PO-9999",
        ),
        "note": "EDGE CASE: vendor not in PO list or ledger; cites a PO that does not exist.",
    },
    {
        "file": "inv_1008_sparkleclean.pdf",
        "doc_type": "invoice",
        "confidence": 0.97,
        "invoice": invoice(
            "SparkleClean Supplies Ltd",
            "INV-1008",
            "2026-09-20",
            "2026-10-20",
            [item("Hand soap refill 1L", 100, 6.00), item("Paper towels (case)", 30, 30.00)],
            0.10,
            "PO-5008",
        ),
        "note": "EDGE CASE: partially paid (1000.00 of 1650.00).",
    },
    {
        "file": "email_brightway_inv_1009.txt",
        "doc_type": "invoice",
        "confidence": 0.91,
        "invoice": invoice(
            "Brightway Uniforms",
            "INV-1009",
            "2026-08-20",
            "2026-09-19",
            [item("Hi-vis jackets", 6, 60.00)],
            0.0,
            "PO-5009",
        ),
        "email": True,
        "note": "Invoice sent in an email body; overdue as of 2026-10-08.",
    },
    {
        "file": "email_newsletter.txt",
        "doc_type": "other",
        "confidence": 0.98,
        "invoice": None,
        "email": True,
        "note": "Supplier newsletter - should be classified `other` and skipped.",
    },
]

LEDGER = [
    ("PAY-001", "2026-09-28", "SparkleClean Supplies Ltd", "INV-1001", 1100.00, "bank_transfer"),
    ("PAY-002", "2026-09-30", "Brightway Uniforms", "INV-1003", 540.00, "bank_transfer"),
    ("PAY-003", "2026-10-02", "QuickFuel Station", "RCPT-2201", 74.71, "card"),
    ("PAY-004", "2026-10-05", "SparkleClean Supplies Ltd", "INV-1008", 1000.00, "bank_transfer"),
    ("PAY-005", "2026-09-12", "Northwind Equipment Hire", "INV-0991", 450.00, "bank_transfer"),
]

PURCHASE_ORDERS = [
    ("PO-5001", "SparkleClean Supplies Ltd", 1100.00, "closed"),
    ("PO-5002", "Metro Janitorial Wholesale", 462.00, "open"),
    ("PO-5003", "Brightway Uniforms", 480.00, "closed"),
    ("PO-5005", "Northwind Equipment Hire", 1080.00, "open"),
    ("PO-5008", "SparkleClean Supplies Ltd", 1650.00, "open"),
    ("PO-5009", "Brightway Uniforms", 360.00, "open"),
]


def write_pdf(sample: dict[str, Any], path: Path) -> None:
    inv = sample["invoice"]
    is_receipt = sample.get("receipt", False)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, inv["vendor"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        6,
        "accounts@" + inv["vendor"].split()[0].lower() + ".example.com",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)
    if sample.get("banner"):
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, sample["banner"], new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "RECEIPT" if is_receipt else "TAX INVOICE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    label = "Receipt No" if is_receipt else "Invoice No"
    pdf.cell(0, 6, f"{label}: {inv['invoice_number']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Date: {inv['date']}", new_x="LMARGIN", new_y="NEXT")
    if inv["due_date"]:
        pdf.cell(0, 6, f"Due date: {inv['due_date']}", new_x="LMARGIN", new_y="NEXT")
    if inv["po_number"]:
        pdf.cell(0, 6, f"Customer PO: {inv['po_number']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.cell(0, 6, "Bill to:" if not is_receipt else "Customer:", new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 5, COMPANY)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    widths = (95, 20, 35, 35)
    for w, h in zip(widths, ("Description", "Qty", "Unit price", "Amount"), strict=True):
        pdf.cell(w, 7, h, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 10)
    for li in inv["line_items"]:
        pdf.cell(widths[0], 7, li["description"], border=1)
        pdf.cell(widths[1], 7, f"{li['quantity']:g}", border=1, align="R")
        pdf.cell(widths[2], 7, f"{li['unit_price']:.2f}", border=1, align="R")
        pdf.cell(widths[3], 7, f"{li['amount']:.2f}", border=1, align="R")
        pdf.ln()
    pdf.ln(2)
    for label, value in (
        ("Subtotal", inv["subtotal"]),
        ("Tax", inv["tax"]),
        ("TOTAL", inv["total"]),
    ):
        pdf.cell(sum(widths[:3]), 7, label, align="R")
        pdf.cell(widths[3], 7, f"{value:.2f} {inv['currency']}", align="R")
        pdf.ln()
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    footer = (
        "Paid by card - thank you."
        if is_receipt
        else "Payment terms: 30 days. Please quote the invoice number on your remittance."
    )
    pdf.cell(0, 6, footer, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def write_emails() -> None:
    inv = next(s for s in SAMPLES if s["file"] == "email_brightway_inv_1009.txt")["invoice"]
    (INBOX / "email_brightway_inv_1009.txt").write_text(
        f"""From: Dana Cole <accounts@brightway.example.com>
To: ap@gleamandgo.example.com
Date: Wed, 20 Aug 2026 09:14:00 +0000
Subject: Invoice {inv["invoice_number"]} - Hi-vis jackets (PO {inv["po_number"]})

Hi team,

Please find below the details for your recent order. The PDF was too large for our
mail gateway so I've pasted the invoice in the body.

Brightway Uniforms
Invoice number: {inv["invoice_number"]}
Invoice date: {inv["date"]}
Due date: {inv["due_date"]}
Your PO: {inv["po_number"]}

Items
- Hi-vis jackets x 6 @ 60.00 = 360.00

Subtotal: 360.00
Tax: 0.00
Total due: 360.00 USD

Bank: Springfield Mutual, acct 12345678, ref {inv["invoice_number"]}

Thanks,
Dana
"""
    )
    (INBOX / "email_newsletter.txt").write_text(
        """From: SparkleClean Supplies <news@sparkleclean.example.com>
To: ap@gleamandgo.example.com
Date: Mon, 6 Oct 2026 08:00:00 +0000
Subject: October offers: 15% off floor care + new eco range

Hello from SparkleClean!

This month only: 15% off all floor care concentrates and free delivery on orders over
500.00. We've also launched a new plant-based eco range - ask your rep for samples.

Reminder: our warehouse is closed on 13 October for stock-take.

You are receiving this because you are a SparkleClean trade customer. Unsubscribe: reply STOP.
"""
    )


def write_csvs() -> None:
    with (DATA / "ledger.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["payment_id", "date", "vendor", "invoice_number", "amount", "method"])
        w.writerows(LEDGER)
    with (DATA / "purchase_orders.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["po_number", "vendor", "amount", "status"])
        w.writerows(PURCHASE_ORDERS)


def write_fixtures() -> None:
    fixtures = {
        s["file"]: {
            "doc_type": s["doc_type"],
            "confidence": s["confidence"],
            "invoice": s["invoice"],
            "notes": s["note"],
        }
        for s in SAMPLES
    }
    FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES.write_text(json.dumps(fixtures, indent=2) + "\n")


def main() -> None:
    INBOX.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    for s in SAMPLES:
        if s["file"].endswith(".pdf"):
            write_pdf(s, INBOX / s["file"])
    write_emails()
    write_csvs()
    write_fixtures()
    print(f"Wrote {len(SAMPLES)} samples to {INBOX}, CSVs to {DATA}, fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
