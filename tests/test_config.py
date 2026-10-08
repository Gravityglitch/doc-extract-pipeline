from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_settings

from docpipe.config import ConfigError, Settings


def test_defaults_are_sqlite_and_folder_mode(tmp_path: Path):
    s = make_settings(tmp_path)
    assert s.is_sqlite and s.should_create_schema
    assert s.ingest_mode == "folder" and s.sink == "csv"


def test_postgres_does_not_auto_create_schema(tmp_path: Path):
    s = make_settings(tmp_path, database_url="postgresql+psycopg://u:p@db/x")
    assert not s.should_create_schema
    assert make_settings(
        tmp_path, database_url="postgresql+psycopg://u:p@db/x", auto_create_schema=True
    ).should_create_schema


def test_imap_mode_requires_credentials(tmp_path: Path):
    with pytest.raises(ValueError, match="DOCPIPE_IMAP_HOST"):
        make_settings(tmp_path, ingest_mode="imap")
    ok = make_settings(
        tmp_path, ingest_mode="imap", imap_host="h", imap_user="u", imap_password="p"
    )
    assert ok.imap_password is not None and "p" not in repr(ok.imap_password)


def test_sheets_sink_requires_spreadsheet_and_credentials(tmp_path: Path):
    with pytest.raises(ValueError, match="DOCPIPE_SINK=sheets"):
        make_settings(tmp_path, sink="sheets")


def test_require_llm_and_api_key(tmp_path: Path):
    s = make_settings(tmp_path, mock=False, api_key=None)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        s.require_llm()
    with pytest.raises(ConfigError, match="DOCPIPE_API_KEY"):
        s.require_api_key()
    make_settings(tmp_path, mock=False, anthropic_api_key="sk-x").require_llm()


def test_env_vars_and_empty_means_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("DOCPIPE_AS_OF", "")
    monkeypatch.setenv("DOCPIPE_RUN_INTERVAL_SECONDS", "42")
    monkeypatch.setenv("DOCPIPE_OUT_DIR", str(tmp_path))
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.database_url == "sqlite:///x.db" and s.as_of is None
    assert s.run_interval_seconds == 42 and s.out_dir == tmp_path
