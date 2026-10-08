"""Deterministic validation of LLM output. Never trust the model's arithmetic."""

from __future__ import annotations

from datetime import date

from docpipe.models import DocType, Invoice, Issue, LLMExtraction

AMOUNT_TOLERANCE = 0.01
CONFIDENCE_THRESHOLD = 0.8


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_invoice(inv: Invoice, doc_type: DocType) -> list[Issue]:
    issues: list[Issue] = []

    if not inv.vendor.strip():
        issues.append(Issue(code="missing_vendor", message="Vendor name is empty"))
    if not inv.invoice_number.strip():
        issues.append(Issue(code="missing_invoice_number", message="Invoice number is empty"))

    if abs(inv.subtotal + inv.tax - inv.total) > AMOUNT_TOLERANCE:
        issues.append(
            Issue(
                code="totals_mismatch",
                message=(
                    f"subtotal {inv.subtotal:.2f} + tax {inv.tax:.2f} != total {inv.total:.2f}"
                ),
            )
        )
    if inv.line_items:
        line_sum = sum(li.amount for li in inv.line_items)
        if abs(line_sum - inv.subtotal) > AMOUNT_TOLERANCE:
            issues.append(
                Issue(
                    code="line_items_mismatch",
                    message=f"line items sum to {line_sum:.2f} but subtotal is {inv.subtotal:.2f}",
                )
            )
    if inv.total <= 0:
        issues.append(Issue(code="non_positive_total", message="Total must be > 0"))

    issued = _parse_date(inv.date)
    if issued is None:
        issues.append(Issue(code="bad_date", message=f"Unparseable issue date: {inv.date!r}"))
    if inv.due_date is not None:
        due = _parse_date(inv.due_date)
        if due is None:
            issues.append(
                Issue(code="bad_due_date", message=f"Unparseable due date: {inv.due_date!r}")
            )
        elif issued is not None and due < issued:
            issues.append(Issue(code="due_before_issue", message="Due date precedes issue date"))

    if doc_type == DocType.INVOICE and not inv.po_number:
        issues.append(
            Issue(code="missing_po", message="No PO number on invoice", severity="warning")
        )
    return issues


def validate(extraction: LLMExtraction) -> tuple[list[Issue], bool]:
    """Return (issues, needs_review)."""
    issues: list[Issue] = []
    if extraction.doc_type in (DocType.INVOICE, DocType.RECEIPT):
        if extraction.invoice is None:
            issues.append(
                Issue(code="no_fields", message="Classified as billable but no fields extracted")
            )
        else:
            issues.extend(validate_invoice(extraction.invoice, extraction.doc_type))
    if extraction.confidence < CONFIDENCE_THRESHOLD:
        issues.append(
            Issue(
                code="low_confidence",
                message=f"Model confidence {extraction.confidence:.2f} < {CONFIDENCE_THRESHOLD}",
                severity="warning",
            )
        )
    needs_review = any(i.severity == "error" for i in issues) or any(
        i.code in ("low_confidence", "missing_po") for i in issues
    )
    return issues, needs_review
