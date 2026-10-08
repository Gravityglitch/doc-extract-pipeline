"""Scheduled runner: a simple loop that runs the pipeline every N seconds.

Deliberately not cron/APScheduler: one process, one env var, restarts cleanly under
docker compose `restart: unless-stopped`. A failing run is logged and the loop carries on.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from docpipe.config import Settings
from docpipe.llm import Extractor
from docpipe.logging_setup import get_logger
from docpipe.pipeline import RunResult, run_with_settings
from docpipe.store import Store

log = get_logger("docpipe.runner")


def run_forever(
    settings: Settings,
    extractor: Extractor,
    store: Store,
    interval_seconds: int | None = None,
    max_iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[RunResult | None]:
    """Run until interrupted (or `max_iterations` passes, for tests). Returns one entry
    per iteration: the RunResult, or None when that iteration raised."""
    interval = interval_seconds or settings.run_interval_seconds
    results: list[RunResult | None] = []
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        started = time.monotonic()
        try:
            results.append(run_with_settings(settings, extractor, store))
        except Exception as exc:
            log.error("run.crashed", error=str(exc), exc_info=True)
            results.append(None)
        if max_iterations is not None and iteration >= max_iterations:
            break
        delay = max(0.0, interval - (time.monotonic() - started))
        log.info("runner.sleep", seconds=round(delay, 1))
        sleep(delay)
    return results
