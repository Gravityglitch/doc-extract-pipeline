"""Review API: list documents, inspect one, approve/reject, read the briefing.

Auth is a single shared secret in the `X-API-Key` header (DOCPIPE_API_KEY). The service
is meant to sit behind a VPN or an authenticating reverse proxy; per-user auth is the
first thing to add if that is not the case.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from docpipe import __version__
from docpipe.config import Settings
from docpipe.models import DocStatus, ProcessedDocument
from docpipe.report import build_briefing
from docpipe.store import Store

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class DecisionIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=2000)


class DocumentSummary(BaseModel):
    id: int
    source_file: str
    status: DocStatus
    doc_type: str
    needs_review: bool
    vendor: str | None
    invoice_number: str | None
    total: float | None
    recon_status: str | None
    issues: list[str]
    error: str | None


def _summary(d: ProcessedDocument) -> DocumentSummary:
    assert d.id is not None
    return DocumentSummary(
        id=d.id,
        source_file=d.source_file,
        status=d.status,
        doc_type=d.doc_type.value,
        needs_review=d.needs_review,
        vendor=d.invoice.vendor if d.invoice else None,
        invoice_number=d.invoice.invoice_number if d.invoice else None,
        total=d.invoice.total if d.invoice else None,
        recon_status=d.reconciliation.status.value if d.reconciliation else None,
        issues=[i.code for i in d.issues],
        error=d.error,
    )


def get_store(request: Request) -> Iterator[Store]:
    yield request.app.state.store


# Module-level so FastAPI can resolve the (string) annotation under `from __future__`.
StoreDep = Annotated[Store, Depends(get_store)]


def create_app(settings: Settings, store: Store | None = None) -> FastAPI:
    expected_key = settings.require_api_key()
    app = FastAPI(title="docpipe review API", version=__version__)
    app.state.store = store or Store(settings.database_url, create=settings.should_create_schema)
    app.state.settings = settings

    def require_key(key: Annotated[str | None, Depends(api_key_header)]) -> None:
        if key is None or not secrets.compare_digest(key, expected_key):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")

    Auth = Depends(require_key)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    def ready(store: StoreDep) -> dict[str, str]:
        try:
            store.ping()
        except Exception as exc:  # pragma: no cover - needs a broken DB
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"db: {exc}") from exc
        return {"status": "ready"}

    @app.get("/documents", dependencies=[Auth])
    def list_documents(
        store: StoreDep,
        doc_status: Annotated[DocStatus | None, Query(alias="status")] = None,
        needs_review: bool | None = None,
    ) -> list[DocumentSummary]:
        return [_summary(d) for d in store.all_documents(doc_status, needs_review)]

    @app.get("/documents/{doc_id}", dependencies=[Auth])
    def get_document(doc_id: int, store: StoreDep) -> ProcessedDocument:
        doc = store.get(doc_id)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
        return doc

    def _decide(doc_id: int, decision: str, body: DecisionIn, store: Store) -> ProcessedDocument:
        current = store.get(doc_id)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
        if current.status == DocStatus.FAILED:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "failed documents must be re-run, not reviewed"
            )
        updated = store.record_decision(doc_id, decision, body.reviewer, body.note)
        assert updated is not None
        return updated

    @app.post("/documents/{doc_id}/approve", dependencies=[Auth])
    def approve(doc_id: int, body: DecisionIn, store: StoreDep) -> ProcessedDocument:
        return _decide(doc_id, "approved", body, store)

    @app.post("/documents/{doc_id}/reject", dependencies=[Auth])
    def reject(doc_id: int, body: DecisionIn, store: StoreDep) -> ProcessedDocument:
        return _decide(doc_id, "rejected", body, store)

    @app.get("/briefing", dependencies=[Auth], response_class=PlainTextResponse)
    def briefing(store: StoreDep, as_of: date | None = None) -> str:
        return build_briefing(store.all_documents(), as_of or date.today())

    return app
