"""Persistence over the SQLAlchemy models: SHA-256 dedupe, results, review decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from docpipe.db import (
    DocumentRow,
    InvoiceRow,
    LineItemRow,
    ReconciliationRow,
    ReviewDecisionRow,
    create_schema,
    make_engine,
)
from docpipe.models import (
    DocStatus,
    DocType,
    Invoice,
    Issue,
    LineItem,
    ProcessedDocument,
    Reconciliation,
    ReconStatus,
    ReviewDecision,
)
from docpipe.reconcile import invoice_key


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


class Store:
    """All DB access goes through here. Construct with a SQLAlchemy URL, a Path to a
    SQLite file (local/test convenience) or an existing Engine."""

    def __init__(self, target: str | Path | Engine, create: bool = True):
        if isinstance(target, Engine):
            self.engine = target
        else:
            if isinstance(target, Path):
                target.parent.mkdir(parents=True, exist_ok=True)
                target = sqlite_url(target)
            self.engine = make_engine(target)
        if create:
            create_schema(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()

    def session(self) -> Session:
        return self._session()

    # --- health -------------------------------------------------------------------

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    # --- dedupe + lookups ---------------------------------------------------------

    def is_processed(self, sha256: str) -> bool:
        with self.session() as s:
            return s.scalar(select(DocumentRow.id).where(DocumentRow.sha256 == sha256)) is not None

    def known_invoice_keys(self) -> set[str]:
        """Invoice keys that count as 'already seen' for duplicate detection: everything
        except documents that were themselves duplicates, failed, or rejected."""
        with self.session() as s:
            rows = s.execute(
                select(DocumentRow.invoice_key)
                .outerjoin(ReconciliationRow)
                .where(DocumentRow.invoice_key.is_not(None))
                .where(DocumentRow.status.not_in([DocStatus.FAILED, DocStatus.REJECTED]))
                .where(
                    (ReconciliationRow.status.is_(None))
                    | (ReconciliationRow.status != ReconStatus.DUPLICATE.value)
                )
            )
            return {r[0] for r in rows}

    def get(self, doc_id: int) -> ProcessedDocument | None:
        with self.session() as s:
            row = s.get(DocumentRow, doc_id)
            return _to_model(row) if row else None

    def get_by_sha(self, sha256: str) -> ProcessedDocument | None:
        with self.session() as s:
            row = s.scalar(select(DocumentRow).where(DocumentRow.sha256 == sha256))
            return _to_model(row) if row else None

    def all_documents(
        self, status: DocStatus | None = None, needs_review: bool | None = None
    ) -> list[ProcessedDocument]:
        stmt = select(DocumentRow).order_by(DocumentRow.source_file)
        if status is not None:
            stmt = stmt.where(DocumentRow.status == status.value)
        if needs_review is not None:
            stmt = stmt.where(DocumentRow.needs_review.is_(needs_review))
        with self.session() as s:
            return [_to_model(r) for r in s.scalars(stmt).unique()]

    # --- writes -------------------------------------------------------------------

    def save(self, doc: ProcessedDocument) -> ProcessedDocument:
        """Insert or replace by sha256. Returns the document with its id set."""
        with self.session() as s:
            existing = s.scalar(select(DocumentRow).where(DocumentRow.sha256 == doc.sha256))
            if existing is not None:
                s.delete(existing)
                s.flush()
            row = _to_row(doc)
            s.add(row)
            s.commit()
            return _to_model(row)

    def record_decision(
        self, doc_id: int, decision: str, reviewer: str, note: str = ""
    ) -> ProcessedDocument | None:
        with self.session() as s:
            row = s.get(DocumentRow, doc_id)
            if row is None:
                return None
            row.decisions.append(
                ReviewDecisionRow(
                    decision=decision,
                    reviewer=reviewer,
                    note=note,
                    decided_at=datetime.now(UTC),
                )
            )
            row.status = DocStatus.APPROVED if decision == "approved" else DocStatus.REJECTED
            row.needs_review = False
            s.commit()
            s.refresh(row)
            return _to_model(row)

    def forget(self, source_file: str | None = None, sha256: str | None = None) -> int:
        """Delete a document (and its children) so the next run reprocesses the file."""
        stmt = delete(DocumentRow)
        if sha256:
            stmt = stmt.where(DocumentRow.sha256 == sha256)
        elif source_file:
            stmt = stmt.where(DocumentRow.source_file == source_file)
        else:
            raise ValueError("forget() needs a source_file or a sha256")
        with self.session() as s:
            ids = s.scalars(select(DocumentRow.id).where(stmt.whereclause)).all()
            for row in s.scalars(select(DocumentRow).where(DocumentRow.id.in_(ids))):
                s.delete(row)
            s.commit()
            return len(ids)

    def reset(self) -> int:
        with self.session() as s:
            rows = s.scalars(select(DocumentRow)).unique().all()
            for row in rows:
                s.delete(row)
            s.commit()
            return len(rows)


# --- mapping helpers ----------------------------------------------------------------


def _to_row(doc: ProcessedDocument) -> DocumentRow:
    row = DocumentRow(
        sha256=doc.sha256,
        source_file=doc.source_file,
        run_id=doc.run_id,
        status=doc.status.value,
        doc_type=doc.doc_type.value,
        extraction_method=doc.extraction_method,
        confidence=doc.confidence,
        needs_review=doc.needs_review,
        invoice_key=invoice_key(doc.invoice) if doc.invoice else None,
        issues=[i.model_dump() for i in doc.issues],
        notes=doc.notes,
        error=doc.error,
        processed_on=doc.processed_on,
    )
    if doc.invoice is not None:
        inv = doc.invoice
        row.invoice = InvoiceRow(
            vendor=inv.vendor,
            invoice_number=inv.invoice_number,
            date=inv.date,
            due_date=inv.due_date,
            subtotal=inv.subtotal,
            tax=inv.tax,
            total=inv.total,
            currency=inv.currency,
            po_number=inv.po_number,
            line_items=[
                LineItemRow(
                    position=i,
                    description=li.description,
                    quantity=li.quantity,
                    unit_price=li.unit_price,
                    amount=li.amount,
                )
                for i, li in enumerate(inv.line_items)
            ],
        )
    if doc.reconciliation is not None:
        rec = doc.reconciliation
        row.reconciliation = ReconciliationRow(
            status=rec.status.value,
            paid_amount=rec.paid_amount,
            po_found=rec.po_found,
            po_amount=rec.po_amount,
            detail=rec.detail,
        )
    row.decisions = [
        ReviewDecisionRow(
            decision=d.decision, reviewer=d.reviewer, note=d.note, decided_at=d.decided_at
        )
        for d in doc.decisions
    ]
    return row


def _to_model(row: DocumentRow) -> ProcessedDocument:
    invoice = None
    if row.invoice is not None:
        r = row.invoice
        invoice = Invoice(
            vendor=r.vendor,
            invoice_number=r.invoice_number,
            date=r.date,
            due_date=r.due_date,
            subtotal=r.subtotal,
            tax=r.tax,
            total=r.total,
            currency=r.currency,
            po_number=r.po_number,
            line_items=[
                LineItem(
                    description=li.description,
                    quantity=li.quantity,
                    unit_price=li.unit_price,
                    amount=li.amount,
                )
                for li in r.line_items
            ],
        )
    recon = None
    if row.reconciliation is not None:
        rc = row.reconciliation
        recon = Reconciliation(
            status=ReconStatus(rc.status),
            paid_amount=rc.paid_amount,
            po_found=rc.po_found,
            po_amount=rc.po_amount,
            detail=rc.detail,
        )
    return ProcessedDocument(
        id=row.id,
        source_file=row.source_file,
        sha256=row.sha256,
        extraction_method=row.extraction_method,  # type: ignore[arg-type]
        doc_type=DocType(row.doc_type),
        confidence=row.confidence,
        invoice=invoice,
        issues=[Issue.model_validate(i) for i in row.issues],
        needs_review=row.needs_review,
        reconciliation=recon,
        processed_on=row.processed_on,
        status=DocStatus(row.status),
        error=row.error,
        run_id=row.run_id,
        notes=row.notes,
        decisions=[
            ReviewDecision(
                decision=d.decision,  # type: ignore[arg-type]
                reviewer=d.reviewer,
                note=d.note,
                decided_at=d.decided_at,
            )
            for d in row.decisions
        ],
    )
