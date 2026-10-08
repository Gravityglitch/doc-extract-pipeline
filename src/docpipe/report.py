"""Writers for out/extracted.jsonl, out/exceptions.csv and out/briefing.md."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from docpipe.models import DocStatus, DocType, ProcessedDocument, ReconStatus

EXCEPTION_STATUSES = {
    ReconStatus.OVERDUE,
    ReconStatus.OVERPAID,
    ReconStatus.DUPLICATE,
    ReconStatus.UNKNOWN_VENDOR,
    ReconStatus.PARTIALLY_PAID,
}


EXCEPTION_COLUMNS = [
    "id",
    "source_file",
    "status",
    "vendor",
    "invoice_number",
    "total",
    "recon_status",
    "needs_review",
    "issues",
    "detail",
]


def is_exception(doc: ProcessedDocument) -> bool:
    """Anything a human should look at: failed, flagged for review, or a reconciliation
    status that is not a plain paid/unpaid."""
    if doc.status == DocStatus.FAILED:
        return True
    if doc.status in (DocStatus.APPROVED, DocStatus.REJECTED):
        return False
    recon_flag = doc.reconciliation is not None and doc.reconciliation.status in EXCEPTION_STATUSES
    return doc.needs_review or recon_flag


def exception_rows(docs: list[ProcessedDocument]) -> list[list[str]]:
    """Header + one row per exception, as plain strings (CSV and Sheets share this)."""
    rows = [EXCEPTION_COLUMNS]
    for d in docs:
        if not is_exception(d):
            continue
        inv = d.invoice
        rows.append(
            [
                str(d.id) if d.id is not None else "",
                d.source_file,
                d.status.value,
                inv.vendor if inv else "",
                inv.invoice_number if inv else "",
                f"{inv.total:.2f}" if inv else "",
                d.reconciliation.status.value if d.reconciliation else "",
                "yes" if d.needs_review else "no",
                "; ".join(i.code for i in d.issues),
                d.error or (d.reconciliation.detail if d.reconciliation else ""),
            ]
        )
    return rows


def write_jsonl(docs: list[ProcessedDocument], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for d in docs:
            f.write(d.model_dump_json() + "\n")


def write_exceptions_csv(docs: list[ProcessedDocument], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(exception_rows(docs))


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
    review_queue = [d for d in docs if d.needs_review and d.status == DocStatus.PROCESSED]
    failed = [d for d in docs if d.status == DocStatus.FAILED]
    exceptions = [d for d in docs if is_exception(d) and d.status != DocStatus.FAILED]

    lines = [
        f"# Daily AP briefing - {as_of.isoformat()}",
        "",
        f"Processed **{len(docs)}** documents: "
        f"{sum(d.doc_type == DocType.INVOICE for d in docs)} invoices, "
        f"{sum(d.doc_type == DocType.RECEIPT for d in docs)} receipts, "
        f"{sum(d.doc_type == DocType.OTHER for d in docs)} other"
        + (f", {len(failed)} failed." if failed else "."),
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

    if failed:
        lines += ["", f"## Failed ({len(failed)} - fix and re-run)", ""]
        for d in failed:
            lines.append(f"- `{d.source_file}` - {d.error}")

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
        reviewed = f" ({d.status.value})" if d.status != DocStatus.PROCESSED else ""
        lines.append(
            f"- {inv.vendor} {inv.invoice_number} - {_money(inv.total, inv.currency)} - "
            f"{d.reconciliation.status.value if d.reconciliation else 'n/a'}{reviewed}"
        )
    return "\n".join(lines) + "\n"


def write_briefing(docs: list[ProcessedDocument], path: Path, as_of: date) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_briefing(docs, as_of)
    path.write_text(text)
    return text
