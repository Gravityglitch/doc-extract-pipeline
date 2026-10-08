from __future__ import annotations

from pathlib import Path

from conftest import ROOT, make_settings
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.llm import FixtureExtractor
from docpipe.runner import run_forever
from docpipe.store import Store

runner = CliRunner()


def test_run_forever_runs_n_iterations_and_survives_a_crash(tmp_path: Path, extractor):
    settings = make_settings(tmp_path)
    store = Store(settings.database_url)
    slept: list[float] = []
    results = run_forever(
        settings, extractor, store, interval_seconds=1, max_iterations=2, sleep=slept.append
    )
    assert len(results) == 2 and results[0] and len(results[0].processed) == 10
    assert results[1] and results[1].processed == [] and len(slept) == 1

    class Broken:
        def extract(self, doc):
            raise RuntimeError("x")

    crashing = make_settings(tmp_path, data_dir=tmp_path / "missing")  # Records.load fails
    assert run_forever(crashing, Broken(), store, max_iterations=1, sleep=slept.append) == [None]
    store.close()


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'cli.db'}",
        "DOCPIPE_OUT_DIR": str(tmp_path / "out"),
        "DOCPIPE_LOG_FORMAT": "console",
    }


def test_cli_run_mock_briefing_rerun_reset(tmp_path: Path):
    env = _env(tmp_path)
    r = runner.invoke(app, ["run", "--mock"], env=env)
    assert r.exit_code == 0, r.output
    assert "processed 10, failed 0, skipped 0" in r.output
    assert (tmp_path / "out" / "briefing.md").exists()

    r = runner.invoke(app, ["run", "--mock"], env=env)
    assert "processed 0, failed 0, skipped 10" in r.output

    r = runner.invoke(app, ["briefing", "--as-of", "2026-10-08"], env=env)
    assert r.exit_code == 0 and "Daily AP briefing - 2026-10-08" in r.output

    r = runner.invoke(app, ["rerun", "inv_1001_sparkleclean.pdf"], env=env)
    assert r.exit_code == 0 and "Forgot 1" in r.output
    r = runner.invoke(app, ["run", "--mock"], env=env)
    assert "processed 1, failed 0, skipped 9" in r.output

    r = runner.invoke(app, ["reset", "--yes"], env=env)
    assert r.exit_code == 0 and "10 documents" in r.output
    assert not (tmp_path / "out" / "briefing.md").exists()


def test_cli_run_without_key_fails_clearly(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = runner.invoke(app, ["run"], env=_env(tmp_path))
    assert r.exit_code != 0 and "ANTHROPIC_API_KEY" in r.output


def test_cli_run_exits_2_when_a_document_fails(tmp_path: Path, monkeypatch):
    def boom(self, doc):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(FixtureExtractor, "extract", boom)
    r = runner.invoke(app, ["run", "--mock"], env=_env(tmp_path))
    assert r.exit_code == 2 and "failed 10" in r.output


def test_cli_api_requires_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DOCPIPE_API_KEY", raising=False)
    r = runner.invoke(app, ["api"], env=_env(tmp_path))
    assert r.exit_code != 0 and "DOCPIPE_API_KEY" in r.output


def test_fixture_dir_is_the_real_sample_set():
    assert len(list((ROOT / "inbox").iterdir())) >= 10
