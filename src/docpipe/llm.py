"""Claude-backed classification + field extraction via tool-use structured output.

The `Extractor` protocol lets tests (and `--mock` runs) swap in deterministic
fixtures so nothing here needs an API key to exercise the rest of the pipeline.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Literal, Protocol

import anthropic

from docpipe.extract import SourceDocument
from docpipe.models import LLMExtraction

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM_PROMPT = """You are an accounts-payable assistant for a commercial cleaning company.
You receive the text (or scanned image) of one document from the shared inbox.

1. Classify it: `invoice` (a bill we must pay), `receipt` (proof of a payment already made,
   e.g. fuel/card receipts) or `other` (newsletters, statements, anything else).
2. For invoices and receipts, extract the fields precisely as printed. Do not invent values:
   if a field (e.g. PO number, due date) is absent, return null. Copy the printed total even if
   the line items do not add up - downstream validation will flag it.
3. Dates must be ISO 8601 (YYYY-MM-DD). Amounts are plain numbers without currency symbols.
4. `confidence` is your overall confidence (0-1) that the classification and amounts are right.
   Lower it for poor scans, ambiguous vendors or hand-written figures.

Always respond by calling the `record_extraction` tool exactly once."""

# Hand-written schema (rather than Invoice.model_json_schema()) so it satisfies the
# strict-mode rules: additionalProperties=false everywhere and every property required.
EXTRACTION_TOOL: dict[str, Any] = {
    "name": "record_extraction",
    "description": "Record the classification and extracted fields for one inbox document.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["doc_type", "confidence", "invoice", "notes"],
        "properties": {
            "doc_type": {"type": "string", "enum": ["invoice", "receipt", "other"]},
            "confidence": {"type": "number"},
            "notes": {"type": "string", "description": "Anything odd a reviewer should know."},
            "invoice": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "vendor",
                            "invoice_number",
                            "date",
                            "due_date",
                            "line_items",
                            "subtotal",
                            "tax",
                            "total",
                            "currency",
                            "po_number",
                        ],
                        "properties": {
                            "vendor": {"type": "string"},
                            "invoice_number": {"type": "string"},
                            "date": {"type": "string"},
                            "due_date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "line_items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["description", "quantity", "unit_price", "amount"],
                                    "properties": {
                                        "description": {"type": "string"},
                                        "quantity": {"type": "number"},
                                        "unit_price": {"type": "number"},
                                        "amount": {"type": "number"},
                                    },
                                },
                            },
                            "subtotal": {"type": "number"},
                            "tax": {"type": "number"},
                            "total": {"type": "number"},
                            "currency": {"type": "string"},
                            "po_number": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        },
                    },
                ]
            },
        },
    },
}

ExtractionMethod = Literal["text", "vision", "fixture"]


class Extractor(Protocol):
    def extract(self, doc: SourceDocument) -> tuple[LLMExtraction, ExtractionMethod]: ...


class ClaudeExtractor:
    """Real extractor. Uses the pypdf text when available; when a PDF yields no text
    (scanned image) it sends the PDF itself as a document block so Claude reads it
    visually. No pdf2image/poppler needed."""

    def __init__(self, model: str | None = None, client: anthropic.Anthropic | None = None):
        self.model = model or os.environ.get("DOCPIPE_MODEL", DEFAULT_MODEL)
        self.client = client or anthropic.Anthropic()

    def extract(self, doc: SourceDocument) -> tuple[LLMExtraction, ExtractionMethod]:
        if doc.text_is_empty and doc.pdf_bytes is not None:
            content: list[dict[str, Any]] = [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(doc.pdf_bytes).decode(),
                    },
                },
                {"type": "text", "text": f"File name: {doc.path.name}. Extract this document."},
            ]
            method: ExtractionMethod = "vision"
        else:
            content = [
                {
                    "type": "text",
                    "text": f"File name: {doc.path.name}\n\n<document>\n{doc.text}\n</document>",
                }
            ]
            method = "text"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            # Forced tool_choice is rejected on current models; the system prompt asks for
            # exactly one tool call and we verify it below.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model refused {doc.path.name}: {response.stop_details}")
        for block in response.content:
            if block.type == "tool_use" and block.name == EXTRACTION_TOOL["name"]:
                return LLMExtraction.model_validate(block.input), method
        raise RuntimeError(f"No record_extraction tool call in response for {doc.path.name}")


class FixtureExtractor:
    """Deterministic extractor for tests and `--mock` runs: looks up results by file name."""

    def __init__(self, fixture_path: Path):
        raw = json.loads(fixture_path.read_text())
        self.fixtures = {name: LLMExtraction.model_validate(v) for name, v in raw.items()}

    def extract(self, doc: SourceDocument) -> tuple[LLMExtraction, ExtractionMethod]:
        try:
            return self.fixtures[doc.path.name], "fixture"
        except KeyError as exc:
            raise KeyError(f"No fixture for {doc.path.name}") from exc
