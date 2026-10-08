from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from docpipe.llm import FixtureExtractor
from docpipe.reconcile import Records

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 8)


@pytest.fixture
def records() -> Records:
    return Records.load(ROOT / "data")


@pytest.fixture
def extractor() -> FixtureExtractor:
    return FixtureExtractor(ROOT / "tests" / "fixtures" / "extractions.json")
