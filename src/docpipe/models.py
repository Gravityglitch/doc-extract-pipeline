"""Pydantic models shared across the pipeline."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DocType(StrEnum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    OTHER = "other"
    UNKNOWN = "unknown"  # extraction failed before the document could be classified


class DocStatus(StrEnum):
    PROCESSED = "processed"  # extracted, validated, reconciled; may still need review
    FAILED = "failed"  # an exception was raised while processing; see `error`
    APPROVED = "approved"  # a reviewer accepted the extraction
    REJECTED = "rejected"  # a reviewer rejected it (wrong fields, not an invoice, ...)


class ReviewDecision(BaseModel):
    """A human decision recorded through the review API."""

    decision: Literal["approved", "rejected"]
    reviewer: str
    note: str = ""
    decided_at: datetime


class LineItem(BaseModel):
    description: str
    quantity: float = 1
    unit_price: float
    amount: float


class Invoice(BaseModel):
    """Structured fields the LLM is asked to fill. Dates are ISO strings so that
    parse failures become validation issues instead of hard crashes."""

    vendor: str
    invoice_number: str
    date: str = Field(description="Issue date, ISO 8601 (YYYY-MM-DD)")
    due_date: str | None = Field(default=None, description="ISO 8601 or null")
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float
    tax: float = 0.0
    total: float
    currency: str = "USD"
    po_number: str | None = None


class LLMExtraction(BaseModel):
    """What the model returns via tool-use: a classification plus, when
    applicable, the extracted invoice/receipt fields."""

    doc_type: DocType
    confidence: float = Field(ge=0, le=1)
    invoice: Invoice | None = None
    notes: str = ""


class Issue(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


class ReconStatus(StrEnum):
    PAID = "paid"
    UNPAID = "unpaid"
    OVERDUE = "overdue"
    PARTIALLY_PAID = "partially_paid"
    OVERPAID = "overpaid"
    DUPLICATE = "duplicate"
    UNKNOWN_VENDOR = "unknown_vendor"
    NOT_APPLICABLE = "n/a"


class Reconciliation(BaseModel):
    status: ReconStatus
    paid_amount: float = 0.0
    po_found: bool = False
    po_amount: float | None = None
    detail: str = ""


class ProcessedDocument(BaseModel):
    """One document's full result: one row of out/extracted.jsonl and the unit the
    review API serves. `id` is assigned by the store."""

    id: int | None = None
    source_file: str
    sha256: str
    extraction_method: Literal["text", "vision", "fixture", "none"]
    doc_type: DocType
    confidence: float
    invoice: Invoice | None
    issues: list[Issue] = Field(default_factory=list)
    needs_review: bool = False
    reconciliation: Reconciliation | None = None
    processed_on: date
    status: DocStatus = DocStatus.PROCESSED
    error: str | None = None
    run_id: str | None = None
    notes: str = ""
    decisions: list[ReviewDecision] = Field(default_factory=list)


class LedgerEntry(BaseModel):
    payment_id: str
    date: str
    vendor: str
    invoice_number: str
    amount: float
    method: str = ""


class PurchaseOrder(BaseModel):
    po_number: str
    vendor: str
    amount: float
    status: str = "open"
