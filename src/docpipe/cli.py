"""`docpipe` command line interface."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import typer
from dotenv import load_dotenv

from docpipe import pipeline
from docpipe.llm import ClaudeExtractor, Extractor, FixtureExtractor

app = typer.Typer(help="LLM document pipeline: inbox -> extraction -> reconciliation -> briefing.")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = ROOT / "tests" / "fixtures" / "extractions.json"
DEMO_AS_OF = date(2026, 10, 8)  # fixed so the sample set reconciles deterministically


def _extractor(mock: bool, model: str | None) -> Extractor:
    if mock:
        return FixtureExtractor(DEFAULT_FIXTURES)
    load_dotenv()
    return ClaudeExtractor(model=model)


@app.command()
def run(
    inbox: Path = typer.Option(ROOT / "inbox", help="Folder of PDFs / .eml / .txt"),
    data: Path = typer.Option(ROOT / "data", help="Folder with ledger.csv + purchase_orders.csv"),
    out: Path = typer.Option(ROOT / "out", help="Output folder"),
    mock: bool = typer.Option(False, help="Use fixture extractions instead of calling Claude"),
    model: str | None = typer.Option(None, help="Override the Claude model id"),
    as_of: str | None = typer.Option(None, help="Reporting date (YYYY-MM-DD); defaults to today"),
    verbose: bool = typer.Option(False, "-v"),
) -> None:
    """Process new inbox files; write extracted.jsonl, exceptions.csv and briefing.md."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")
    report_date = date.fromisoformat(as_of) if as_of else (DEMO_AS_OF if mock else date.today())
    result = pipeline.run(inbox, data, out, _extractor(mock, model), as_of=report_date)
    typer.echo(f"Processed {len(result.processed)} new file(s), skipped {len(result.skipped)}.")
    typer.echo(f"Outputs in {out}/ (extracted.jsonl, exceptions.csv, briefing.md)")


@app.command()
def briefing(
    out: Path = typer.Option(ROOT / "out"),
    as_of: str | None = typer.Option(None, help="Reporting date (YYYY-MM-DD)"),
) -> None:
    """Regenerate and print the briefing from the SQLite store."""
    report_date = date.fromisoformat(as_of) if as_of else DEMO_AS_OF
    typer.echo(pipeline.briefing_only(out, report_date))


@app.command()
def reset(out: Path = typer.Option(ROOT / "out")) -> None:
    """Delete the SQLite store and outputs so the next run reprocesses everything."""
    for name in ("docpipe.db", "extracted.jsonl", "exceptions.csv", "briefing.md"):
        (out / name).unlink(missing_ok=True)
    typer.echo("Store cleared.")


if __name__ == "__main__":
    app()
