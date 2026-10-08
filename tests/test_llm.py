"""ClaudeExtractor is exercised with a stubbed client so no API key is needed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from conftest import ROOT

from docpipe.extract import SourceDocument, load_document
from docpipe.llm import EXTRACTION_TOOL, ClaudeExtractor
from docpipe.models import DocType


class StubMessages:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", name=EXTRACTION_TOOL["name"], input=self.payload)
        return SimpleNamespace(stop_reason="tool_use", content=[block], stop_details=None)


def make_client(payload: dict):
    return SimpleNamespace(messages=StubMessages(payload))


def test_text_path_sends_document_text_and_parses_tool_call():
    fixtures = json.loads((ROOT / "tests" / "fixtures" / "extractions.json").read_text())
    client = make_client(fixtures["inv_1001_sparkleclean.pdf"])
    extractor = ClaudeExtractor(model="claude-sonnet-5-5", client=client)

    extraction, method = extractor.extract(
        load_document(ROOT / "inbox" / "inv_1001_sparkleclean.pdf")
    )

    assert method == "text"
    assert extraction.doc_type == DocType.INVOICE and extraction.invoice.total == 1100
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["tools"][0]["strict"] is True
    assert "INV-1001" in call["messages"][0]["content"][0]["text"]


def test_empty_pdf_text_falls_back_to_vision():
    payload = {"doc_type": "other", "confidence": 0.5, "invoice": None, "notes": ""}
    client = make_client(payload)
    extractor = ClaudeExtractor(client=client)
    doc = SourceDocument(Path("scan.pdf"), "abc", "   ", b"%PDF-1.4 fake")

    _, method = extractor.extract(doc)

    assert method == "vision"
    first_block = client.messages.calls[0]["messages"][0]["content"][0]
    assert first_block["type"] == "document"
    assert first_block["source"]["media_type"] == "application/pdf"
