"""Pydantic models shared across the pipeline."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DocType(StrEnum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    OTHER = "other"


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
    """One row of out/extracted.jsonl."""

    source_file: str
    sha256: str
    extraction_method: Literal["text", "vision", "fixture"]
    doc_type: DocType
    confidence: float
    invoice: Invoice | None
    issues: list[Issue] = Field(default_factory=list)
    needs_review: bool = False
    reconciliation: Reconciliation | None = None
    processed_on: date


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
