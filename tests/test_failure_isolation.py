from __future__ import annotations

import json
from pathlib import Path

from conftest import AS_OF, ROOT

from docpipe import pipeline
from docpipe.extract import SourceDocument
from docpipe.llm import FixtureExtractor
from docpipe.models import DocStatus
from docpipe.store import Store

BAD = "inv_1005_northwind_equipment.pdf"


class Flaky:
    """Raises for one named file, delegates everything else to the fixtures."""

    def __init__(self, inner: FixtureExtractor, bad: str, fail: bool = True):
        self.inner, self.bad, self.fail = inner, bad, fail

    def extract(self, doc: SourceDocument):
        if self.fail and doc.path.name == self.bad:
            raise RuntimeError("model refused: synthetic failure")
        return self.inner.extract(doc)


def test_one_bad_document_does_not_abort_the_run(tmp_path: Path, extractor):
    out = tmp_path / "out"
    flaky = Flaky(extractor, BAD)
    result = pipeline.run(ROOT / "inbox", ROOT / "data", out, flaky, as_of=AS_OF)

    assert len(result.processed) == 9 and len(result.failed) == 1
    failed = result.failed[0]
    assert failed.source_file == BAD and failed.status == DocStatus.FAILED
    assert "synthetic failure" in (failed.error or "") and failed.run_id == result.run_id

    store = Store(out / "docpipe.db")
    stored = store.get_by_sha(failed.sha256)
    assert stored and stored.status == DocStatus.FAILED and stored.needs_review
    store.close()

    rows = [json.loads(line) for line in (out / "extracted.jsonl").read_text().splitlines()]
    assert sum(r["status"] == "failed" for r in rows) == 1
    csv_text = (out / "exceptions.csv").read_text()
    assert f"{BAD},failed" in csv_text and "synthetic failure" in csv_text
    assert "## Failed (1" in result.briefing and BAD in result.briefing


def test_failed_document_is_retried_after_rerun(tmp_path: Path, extractor):
    out = tmp_path / "out"
    flaky = Flaky(extractor, BAD)
    pipeline.run(ROOT / "inbox", ROOT / "data", out, flaky, as_of=AS_OF)

    # Still failed -> still skipped (hash is known), until we forget it.
    again = pipeline.run(ROOT / "inbox", ROOT / "data", out, flaky, as_of=AS_OF)
    assert again.processed == [] and again.failed == []

    store = Store(out / "docpipe.db")
    assert store.forget(source_file=BAD) == 1
    store.close()
    flaky.fail = False
    fixed = pipeline.run(ROOT / "inbox", ROOT / "data", out, flaky, as_of=AS_OF)
    assert [d.source_file for d in fixed.processed] == [BAD]
    assert fixed.processed[0].status == DocStatus.PROCESSED
    assert "## Failed" not in fixed.briefing


def test_unreadable_file_lands_in_exceptions(tmp_path: Path, extractor):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "corrupt.pdf").write_bytes(b"not a pdf at all")
    result = pipeline.run(inbox, ROOT / "data", tmp_path / "out", extractor, as_of=AS_OF)
    assert len(result.failed) == 1 and result.failed[0].extraction_method == "none"
