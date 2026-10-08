from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from docpipe.config import Settings
from docpipe.llm import FixtureExtractor
from docpipe.reconcile import Records
from docpipe.store import Store

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 8)
API_KEY = "test-key"


@pytest.fixture
def records() -> Records:
    return Records.load(ROOT / "data")


@pytest.fixture
def extractor() -> FixtureExtractor:
    return FixtureExtractor(ROOT / "tests" / "fixtures" / "extractions.json")


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings pointed at a temp SQLite DB and temp output folder, env ignored."""
    values: dict[str, object] = {
        "database_url": f"sqlite:///{tmp_path / 'docpipe.db'}",
        "inbox_dir": ROOT / "inbox",
        "data_dir": ROOT / "data",
        "out_dir": tmp_path / "out",
        "mock": True,
        "as_of": AS_OF,
        "api_key": API_KEY,
        "log_format": "console",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def store(settings: Settings):
    s = Store(settings.database_url)
    yield s
    s.close()
