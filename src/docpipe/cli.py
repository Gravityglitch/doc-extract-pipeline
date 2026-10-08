"""`docpipe` command line interface."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from docpipe import pipeline
from docpipe.config import ConfigError, Settings, load_settings
from docpipe.llm import ClaudeExtractor, Extractor, FixtureExtractor
from docpipe.logging_setup import configure_logging, get_logger
from docpipe.store import Store

app = typer.Typer(help="LLM document pipeline: inbox -> extraction -> reconciliation -> briefing.")
log = get_logger("docpipe.cli")

DEMO_AS_OF = date(2026, 10, 8)  # fixed so the sample set reconciles deterministically


def _settings(**overrides: object) -> Settings:
    clean = {k: v for k, v in overrides.items() if v is not None}
    try:
        settings = load_settings(**clean)
    except (ValueError, ConfigError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    configure_logging(settings.log_level, settings.log_format)
    return settings


def _extractor(settings: Settings) -> Extractor:
    if settings.mock:
        return FixtureExtractor(settings.fixtures_file)
    try:
        settings.require_llm()
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return ClaudeExtractor(
        model=settings.model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _store(settings: Settings) -> Store:
    return Store(settings.database_url, create=settings.should_create_schema)


def _as_of(settings: Settings, as_of: str | None) -> date | None:
    if as_of:
        return date.fromisoformat(as_of)
    if settings.as_of:
        return settings.as_of
    return DEMO_AS_OF if settings.mock else None


@app.command()
def run(
    inbox: Path | None = typer.Option(None, help="Folder of PDFs / .eml / .txt"),
    data: Path | None = typer.Option(None, help="Folder with ledger.csv + purchase_orders.csv"),
    out: Path | None = typer.Option(None, help="Output folder"),
    mock: bool = typer.Option(False, help="Use fixture extractions instead of calling Claude"),
    model: str | None = typer.Option(None, help="Override the Claude model id"),
    as_of: str | None = typer.Option(None, help="Reporting date (YYYY-MM-DD); defaults to today"),
    verbose: bool = typer.Option(False, "-v", help="Console logs at DEBUG"),
) -> None:
    """One pass: poll IMAP (if configured), process new inbox files, write outputs."""
    settings = _settings(
        inbox_dir=inbox,
        data_dir=data,
        out_dir=out,
        mock=mock or None,
        model=model,
        **({"log_level": "DEBUG", "log_format": "console"} if verbose else {}),
    )
    settings = settings.model_copy(update={"as_of": _as_of(settings, as_of)})
    store = _store(settings)
    try:
        result = pipeline.run_with_settings(settings, _extractor(settings), store)
    finally:
        store.close()
    typer.echo(
        f"run {result.run_id}: processed {len(result.processed)}, failed {len(result.failed)}, "
        f"skipped {len(result.skipped)}."
    )
    typer.echo(f"Outputs in {settings.out_dir}/ (extracted.jsonl, exceptions.csv, briefing.md)")
    if result.failed:
        raise typer.Exit(code=2)


@app.command()
def loop(
    interval: int | None = typer.Option(None, help="Seconds between runs"),
    mock: bool = typer.Option(False),
) -> None:
    """Run the pipeline forever on a fixed interval (the docker-compose runner)."""
    from docpipe.runner import run_forever

    settings = _settings(run_interval_seconds=interval, mock=mock or None)
    store = _store(settings)
    try:
        run_forever(settings, _extractor(settings), store)
    finally:
        store.close()


@app.command()
def poll() -> None:
    """Pull mail from the configured IMAP mailbox into the inbox folder (no processing)."""
    from docpipe.ingest.imap import poll_from_settings

    settings = _settings(ingest_mode="imap")
    result = poll_from_settings(settings)
    typer.echo(f"Fetched {result.fetched}, moved {result.moved}, saved {len(result.saved)} files.")
    if result.errors:
        typer.echo("Errors: " + "; ".join(result.errors), err=True)
        raise typer.Exit(code=2)


@app.command()
def api(
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None),
) -> None:
    """Serve the review API (FastAPI + uvicorn)."""
    import uvicorn

    from docpipe.api import create_app

    settings = _settings(api_host=host, api_port=port)
    try:
        settings.require_api_key()
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


@app.command()
def briefing(
    out: Path | None = typer.Option(None),
    as_of: str | None = typer.Option(None, help="Reporting date (YYYY-MM-DD)"),
) -> None:
    """Regenerate and print the briefing from the store."""
    settings = _settings(out_dir=out)
    store = _store(settings)
    try:
        typer.echo(
            pipeline.briefing_only(
                settings.out_dir, _as_of(settings, as_of) or date.today(), store=store
            )
        )
    finally:
        store.close()


@app.command()
def rerun(source_file: str = typer.Argument(help="File name as shown in the briefing")) -> None:
    """Forget one document so the next `run` reprocesses it (e.g. after fixing a failure)."""
    settings = _settings()
    store = _store(settings)
    try:
        n = store.forget(source_file=source_file)
    finally:
        store.close()
    typer.echo(f"Forgot {n} document(s) named {source_file!r}; run `docpipe run` to reprocess.")


@app.command()
def reset(
    out: Path | None = typer.Option(None),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Delete every stored document and the output files so the next run starts clean."""
    settings = _settings(out_dir=out)
    if not yes and not settings.is_sqlite:
        typer.confirm(f"Delete ALL rows in {settings.database_url}?", abort=True)
    store = _store(settings)
    try:
        n = store.reset()
    finally:
        store.close()
    for name in ("extracted.jsonl", "exceptions.csv", "briefing.md"):
        (settings.out_dir / name).unlink(missing_ok=True)
    typer.echo(f"Store cleared ({n} documents).")


if __name__ == "__main__":
    app()
