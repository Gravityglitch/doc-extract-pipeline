"""Writers for out/extracted.jsonl, out/exceptions.csv and out/briefing.md."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from docpipe.models import DocType, ProcessedDocument, ReconStatus

EXCEPTION_STATUSES = {
    ReconStatus.OVERDUE,
    ReconStatus.OVERPAID,
    ReconStatus.DUPLICATE,
    ReconStatus.UNKNOWN_VENDOR,
    ReconStatus.PARTIALLY_PAID,
}


def is_exception(doc: ProcessedDocument) -> bool:
    recon_flag = doc.reconciliation is not None and doc.reconciliation.status in EXCEPTION_STATUSES
    return doc.needs_review or recon_flag


def write_jsonl(docs: list[ProcessedDocument], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for d in docs:
            f.write(d.model_dump_json() + "\n")


def write_exceptions_csv(docs: list[ProcessedDocument], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "source_file",
                "vendor",
                "invoice_number",
                "total",
                "recon_status",
                "needs_review",
                "issues",
                "detail",
            ]
        )
        for d in docs:
            if not is_exception(d):
                continue
            inv = d.invoice
            w.writerow(
                [
                    d.source_file,
                    inv.vendor if inv else "",
                    inv.invoice_number if inv else "",
                    f"{inv.total:.2f}" if inv else "",
                    d.reconciliation.status.value if d.reconciliation else "",
                    "yes" if d.needs_review else "no",
                    "; ".join(i.code for i in d.issues),
                    d.reconciliation.detail if d.reconciliation else "",
                ]
            )


def _money(amount: float, currency: str = "USD") -> str:
    return f"{amount:,.2f} {currency}"


def build_briefing(docs: list[ProcessedDocument], as_of: date) -> str:
    billable = [d for d in docs if d.invoice is not None]
    statuses = Counter(d.reconciliation.status for d in billable if d.reconciliation)
    outstanding = sum(
        d.invoice.total - d.reconciliation.paid_amount
        for d in billable
        if d.reconciliation
        and d.reconciliation.status
        in (ReconStatus.UNPAID, ReconStatus.OVERDUE, ReconStatus.PARTIALLY_PAID)
    )
    review_queue = [d for d in docs if d.needs_review]
    exceptions = [d for d in docs if is_exception(d)]

    lines = [
        f"# Daily AP briefing - {as_of.isoformat()}",
        "",
        f"Processed **{len(docs)}** documents: "
        f"{sum(d.doc_type == DocType.INVOICE for d in docs)} invoices, "
        f"{sum(d.doc_type == DocType.RECEIPT for d in docs)} receipts, "
        f"{sum(d.doc_type == DocType.OTHER for d in docs)} other.",
        "",
        "## Payment status",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status in ReconStatus:
        if statuses.get(status):
            lines.append(f"| {status.value} | {statuses[status]} |")
    lines += ["", f"**Outstanding payable:** {_money(outstanding)}", ""]

    lines += ["## Needs attention", ""]
    if not exceptions:
        lines.append("Nothing to flag today.")
    else:
        lines += [
            "| File | Vendor | Invoice | Total | Status | Why |",
            "|---|---|---|---|---|---|",
        ]
        for d in exceptions:
            inv = d.invoice
            why = (
                d.reconciliation.detail
                if d.reconciliation and d.reconciliation.status in EXCEPTION_STATUSES
                else "; ".join(i.message for i in d.issues)
            )
            lines.append(
                f"| {d.source_file} | {inv.vendor if inv else '-'} | "
                f"{inv.invoice_number if inv else '-'} | "
                f"{_money(inv.total, inv.currency) if inv else '-'} | "
                f"{d.reconciliation.status.value if d.reconciliation else '-'} | {why} |"
            )

    lines += ["", f"## Approval queue ({len(review_queue)} awaiting human review)", ""]
    if not review_queue:
        lines.append("Empty.")
    for d in review_queue:
        issues = ", ".join(i.code for i in d.issues) or "n/a"
        lines.append(f"- [ ] `{d.source_file}` - confidence {d.confidence:.2f}; issues: {issues}")

    lines += ["", "## Clean", ""]
    clean = [d for d in docs if not is_exception(d) and d.invoice is not None]
    if not clean:
        lines.append("None.")
    for d in clean:
        inv = d.invoice
        assert inv is not None
        lines.append(
            f"- {inv.vendor} {inv.invoice_number} - {_money(inv.total, inv.currency)} - "
            f"{d.reconciliation.status.value if d.reconciliation else 'n/a'}"
        )
    return "\n".join(lines) + "\n"


def write_briefing(docs: list[ProcessedDocument], path: Path, as_of: date) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_briefing(docs, as_of)
    path.write_text(text)
    return text
