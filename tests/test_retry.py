"""tenacity retry path around the Claude call, with a stubbed client and zero backoff."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from docpipe.extract import SourceDocument
from docpipe.llm import EXTRACTION_TOOL, ClaudeExtractor, estimate_cost_usd

PAYLOAD = {"doc_type": "other", "confidence": 0.9, "invoice": None, "notes": ""}
DOC = SourceDocument(Path("x.txt"), "abc", "hello", None)


def _http_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("synthetic", response=httpx2.Response(code, request=request), body=None)


class FlakyMessages:
    def __init__(self, failures: list[Exception]):
        self.failures = failures
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        block = SimpleNamespace(type="tool_use", name=EXTRACTION_TOOL["name"], input=PAYLOAD)
        usage = SimpleNamespace(input_tokens=1200, output_tokens=80)
        return SimpleNamespace(
            stop_reason="tool_use", content=[block], usage=usage, _request_id="req_1"
        )


def _extractor(failures: list[Exception], max_retries: int = 3) -> ClaudeExtractor:
    client = SimpleNamespace(messages=FlakyMessages(failures))
    return ClaudeExtractor(
        model="claude-sonnet-5-5",
        client=client,  # type: ignore[arg-type]
        max_retries=max_retries,
        backoff_initial=0,
        backoff_max=0,
    )


def test_transient_errors_are_retried_then_succeed():
    ex = _extractor(
        [
            _http_error(anthropic.RateLimitError, 429),
            _http_error(anthropic.InternalServerError, 503),
            anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")),
        ]
    )
    extraction, method = ex.extract(DOC)
    assert extraction.doc_type == "other" and method == "text"
    assert ex.client.messages.calls == 4 and ex.usage.retries == 3
    assert ex.usage.calls == 1 and ex.usage.input_tokens == 1200
    assert ex.usage.cost_usd == pytest.approx(0.0032) and ex.usage.by_file["x.txt"] > 0


def test_retries_are_exhausted():
    ex = _extractor([_http_error(anthropic.RateLimitError, 429)] * 5, max_retries=2)
    with pytest.raises(anthropic.RateLimitError):
        ex.extract(DOC)
    assert ex.client.messages.calls == 3


def test_permanent_errors_are_not_retried():
    ex = _extractor([_http_error(anthropic.BadRequestError, 400)])
    with pytest.raises(anthropic.BadRequestError):
        ex.extract(DOC)
    assert ex.client.messages.calls == 1


def test_refusal_and_missing_tool_call_raise():
    class Refusing:
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", content=[], stop_details="cyber")

    ex = ClaudeExtractor(client=SimpleNamespace(messages=Refusing()))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="refused"):
        ex.extract(DOC)

    class NoTool:
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="end_turn", content=[], stop_details=None)

    ex = ClaudeExtractor(client=SimpleNamespace(messages=NoTool()))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="No record_extraction"):
        ex.extract(DOC)


def test_cost_estimate_unknown_model_is_zero():
    assert estimate_cost_usd("claude-sonnet-5-5", 1_000_000, 0) == 2.0
    assert estimate_cost_usd("some-future-model", 1_000_000, 1_000_000) == 0.0
