"""structlog configuration: JSON lines in production, coloured console locally.

Every log line carries the current `run_id` (bound via contextvars) so a single
pipeline run can be grepped out of aggregated logs.
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Literal

import structlog


class _StderrLogger:
    """Looks up sys.stderr at write time so redirected/captured streams are honoured."""

    def msg(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    log = debug = info = warning = error = critical = exception = msg


def _stderr_factory(*args: object) -> _StderrLogger:
    return _StderrLogger()


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def configure_logging(level: str = "INFO", fmt: Literal["json", "console"] = "json") -> None:
    renderer: structlog.types.Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        renderer,
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=_stderr_factory,
        cache_logger_on_first_use=False,
    )
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()  # resolves sys.stderr lazily on emit
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())


def bind_run(run_id: str) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id)


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(component=name)  # lazy proxy: honours configure() called later
