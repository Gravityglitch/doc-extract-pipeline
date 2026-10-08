"""Claude-backed classification + field extraction via tool-use structured output.

The `Extractor` protocol lets tests (and `--mock` runs) swap in deterministic
fixtures so nothing here needs an API key to exercise the rest of the pipeline.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import anthropic
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from docpipe.extract import SourceDocument
from docpipe.logging_setup import get_logger
from docpipe.models import LLMExtraction

DEFAULT_MODEL = "claude-sonnet-5-5"

# USD per million tokens (input, output); used only for the cost estimate in logs.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Transient failures worth retrying: network, timeout, 429 and 5xx. 4xx (bad request,
# auth, refusal) are permanent and surface immediately.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    anthropic.APIConnectionError,  # includes APITimeoutError
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

log = get_logger("docpipe.llm")


@dataclass
class UsageTotals:
    """Accumulated token usage and estimated spend for one extractor instance."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    retries: int = 0
    by_file: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost
        self.by_file[name] = self.by_file.get(name, 0.0) + cost


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = PRICING_PER_MTOK.get(model, (0.0, 0.0))
    return round((input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 6)


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

_STD_LOG = logging.getLogger("docpipe.llm.retry")


class Extractor(Protocol):
    def extract(self, doc: SourceDocument) -> tuple[LLMExtraction, ExtractionMethod]: ...


class ClaudeExtractor:
    """Real extractor. Uses the pypdf text when available; when a PDF yields no text
    (scanned image) it sends the PDF itself as a document block so Claude reads it
    visually. No pdf2image/poppler needed.

    Reliability: the SDK client is created with `max_retries=0` and a hard timeout, and
    tenacity owns retrying (exponential backoff with jitter) so attempts are visible in
    the logs. Token usage and an estimated cost are logged per call and accumulated in
    `self.usage`."""

    def __init__(
        self,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ):
        self.model = model or os.environ.get("DOCPIPE_MODEL", DEFAULT_MODEL)
        self.client = client or anthropic.Anthropic(timeout=timeout, max_retries=0)
        self.max_retries = max_retries
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.usage = UsageTotals()

    def _retrying(self) -> Retrying:
        return Retrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential_jitter(multiplier=self.backoff_initial, max=self.backoff_max),
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            before_sleep=before_sleep_log(_STD_LOG, logging.WARNING),
            reraise=True,
        )

    def _create(self, **kwargs: Any) -> Any:
        for attempt in self._retrying():
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    self.usage.retries += 1
                return self.client.messages.create(**kwargs)
        raise AssertionError("unreachable")  # pragma: no cover

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

        started = time.perf_counter()
        response = self._create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            # Forced tool_choice is rejected on current models; the system prompt asks for
            # exactly one tool call and we verify it below.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=[{"role": "user", "content": content}],
        )
        self._log_usage(doc, method, response, time.perf_counter() - started)
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model refused {doc.path.name}: {response.stop_details}")
        for block in response.content:
            if block.type == "tool_use" and block.name == EXTRACTION_TOOL["name"]:
                return LLMExtraction.model_validate(block.input), method
        raise RuntimeError(f"No record_extraction tool call in response for {doc.path.name}")

    def _log_usage(
        self, doc: SourceDocument, method: ExtractionMethod, response: Any, seconds: float
    ) -> None:
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = estimate_cost_usd(self.model, input_tokens, output_tokens)
        self.usage.add(doc.path.name, input_tokens, output_tokens, cost)
        log.info(
            "llm.call",
            file=doc.path.name,
            model=self.model,
            method=method,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=round(seconds * 1000),
            request_id=getattr(response, "_request_id", None),
            stop_reason=getattr(response, "stop_reason", None),
        )


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
