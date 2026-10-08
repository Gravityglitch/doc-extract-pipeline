from __future__ import annotations

import pytest
from conftest import API_KEY, AS_OF, ROOT
from fastapi.testclient import TestClient

from docpipe import pipeline
from docpipe.api import create_app
from docpipe.models import DocStatus
from docpipe.pipeline import failed_document

HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture
def client(settings, store, extractor) -> TestClient:
    pipeline.run(settings.inbox_dir, settings.data_dir, settings.out_dir, extractor, AS_OF, store)
    store.save(
        failed_document(ROOT / "inbox" / "ghost.pdf", "f" * 64, RuntimeError("boom"), AS_OF, "r")
    )
    return TestClient(create_app(settings, store))


def test_health_and_ready_are_public(client: TestClient):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json() == {"status": "ready"}


def test_auth_required(client: TestClient):
    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/briefing", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/documents", headers=HEADERS).status_code == 200


def test_list_and_filters(client: TestClient):
    docs = client.get("/documents", headers=HEADERS).json()
    assert len(docs) == 11 and {"id", "source_file", "status", "needs_review"} <= docs[0].keys()
    review = client.get("/documents", params={"needs_review": "true"}, headers=HEADERS).json()
    assert {d["source_file"] for d in review} == {
        "ghost.pdf",
        "inv_1002_metro_janitorial.pdf",
        "inv_1002_metro_janitorial_resend.pdf",
        "inv_1005_northwind_equipment.pdf",
    }
    failed = client.get("/documents", params={"status": "failed"}, headers=HEADERS).json()
    assert [d["source_file"] for d in failed] == ["ghost.pdf"] and failed[0]["error"]
    assert client.get("/documents", params={"status": "bogus"}, headers=HEADERS).status_code == 422


def test_get_document(client: TestClient):
    doc_id = client.get("/documents", headers=HEADERS).json()[0]["id"]
    body = client.get(f"/documents/{doc_id}", headers=HEADERS).json()
    assert body["id"] == doc_id and "invoice" in body and "reconciliation" in body
    assert client.get("/documents/9999", headers=HEADERS).status_code == 404


def test_approve_and_reject(client: TestClient, store):
    review = client.get("/documents", params={"needs_review": "true"}, headers=HEADERS).json()
    by_name = {d["source_file"]: d["id"] for d in review}

    r = client.post(
        f"/documents/{by_name['inv_1005_northwind_equipment.pdf']}/approve",
        json={"reviewer": "vikrant", "note": "vendor confirmed 1180"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved" and r.json()["needs_review"] is False
    assert r.json()["decisions"][0]["note"] == "vendor confirmed 1180"

    r = client.post(
        f"/documents/{by_name['inv_1002_metro_janitorial_resend.pdf']}/reject",
        json={"reviewer": "vikrant"},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"

    stored = store.get(by_name["inv_1005_northwind_equipment.pdf"])
    assert stored and stored.status == DocStatus.APPROVED
    remaining = client.get("/documents", params={"needs_review": "true"}, headers=HEADERS).json()
    assert len(remaining) == 2

    # Validation and edge cases.
    assert client.post(f"/documents/{by_name['ghost.pdf']}/approve", json={"reviewer": "x"},
                       headers=HEADERS).status_code == 409  # fmt: skip
    assert client.post("/documents/9999/approve", json={"reviewer": "x"},
                       headers=HEADERS).status_code == 404  # fmt: skip
    assert client.post(f"/documents/{by_name['ghost.pdf']}/reject", json={},
                       headers=HEADERS).status_code == 422  # fmt: skip


def test_briefing_reflects_reviews(client: TestClient):
    text = client.get("/briefing", params={"as_of": "2026-10-08"}, headers=HEADERS).text
    assert text.startswith("# Daily AP briefing - 2026-10-08")
    assert "Approval queue (3" in text and "ghost.pdf" in text
