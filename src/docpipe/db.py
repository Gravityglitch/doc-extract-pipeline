"""SQLAlchemy 2.x ORM models. Postgres in production, SQLite locally and in tests.

`documents` is the system of record. Invoice fields, line items and the reconciliation
result are normalised into their own tables so they can be queried and joined (e.g. from
a BI tool); `issues` stays as a JSON column because its shape is open-ended.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    source_file: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    invoice_key: Mapped[str | None] = mapped_column(String(512), index=True)
    issues: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text)
    processed_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    invoice: Mapped[InvoiceRow | None] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False, lazy="joined"
    )
    reconciliation: Mapped[ReconciliationRow | None] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False, lazy="joined"
    )
    decisions: Mapped[list[ReviewDecisionRow]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ReviewDecisionRow.id",
        lazy="selectin",
    )


class InvoiceRow(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    vendor: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(32), nullable=False)
    due_date: Mapped[str | None] = mapped_column(String(32))
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)
    tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    po_number: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[DocumentRow] = relationship(back_populates="invoice")
    line_items: Mapped[list[LineItemRow]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="LineItemRow.position",
        lazy="selectin",
    )


class LineItemRow(Base):
    __tablename__ = "line_items"
    __table_args__ = (UniqueConstraint("invoice_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    invoice: Mapped[InvoiceRow] = relationship(back_populates="line_items")


class ReconciliationRow(Base):
    __tablename__ = "reconciliation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    paid_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    po_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    po_amount: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    document: Mapped[DocumentRow] = relationship(back_populates="reconciliation")


class ReviewDecisionRow(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document: Mapped[DocumentRow] = relationship(back_populates="decisions")


def make_engine(database_url: str) -> Engine:
    """Engine factory. SQLite gets `check_same_thread=False` so the FastAPI threadpool
    can share it; Postgres gets `pool_pre_ping` so stale connections are recycled."""
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url, pool_pre_ping=True)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
