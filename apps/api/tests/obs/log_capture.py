"""Capture every log record of every logger at DEBUG, as the process would emit it (INV-5)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from abb_api.core.logging import JsonFormatter, configure_logging


@dataclass
class Captured:
    """Everything a log record can carry: the line as emitted and each raw part of the record."""

    lines: list[str] = field(default_factory=list)
    records: list[logging.LogRecord] = field(default_factory=list)
    extra_text: list[str] = field(default_factory=list)  # CLI stdout / stderr

    def haystack(self) -> str:
        parts = list(self.lines) + self.extra_text
        for record in self.records:
            parts.append(record.getMessage())
            parts.append(repr(record.args))
            parts.append(str(record.__dict__.get("exc_text")))
            parts.append(repr(record.name))
            for key, value in record.__dict__.items():
                if key not in _STANDARD:
                    parts.append(f"{key}={value!r}")
        return "\n".join(parts)


_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class _Handler(logging.Handler):
    def __init__(self, captured: Captured) -> None:
        super().__init__(level=logging.DEBUG)
        self._captured = captured
        self._formatter = JsonFormatter("abb-test")

    def emit(self, record: logging.LogRecord) -> None:
        self._captured.records.append(record)
        self._captured.lines.append(self._formatter.format(record))


@contextmanager
def capture_everything() -> Iterator[Captured]:
    """Run the production logging configuration at DEBUG, then collect instead of printing.

    `configure_logging` is called for real so its pins (SQL and wire loggers, httpx) apply: this
    is what an operator who sets the level to DEBUG gets.
    """
    root = logging.getLogger()
    before = (root.handlers[:], root.level, logging.getLogger("uvicorn.access").disabled)
    levels = {n: logging.getLogger(n).level for n in _PINNED}
    captured = Captured()
    configure_logging("DEBUG")
    root.handlers[:] = [_Handler(captured)]
    try:
        yield captured
    finally:
        root.handlers[:] = before[0]
        root.setLevel(before[1])
        logging.getLogger("uvicorn.access").disabled = before[2]
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


_PINNED = ("sqlalchemy", "asyncpg", "asyncio", "httpx", "httpcore")


def find_leaks(captured: Captured, secrets: set[str]) -> dict[str, int]:
    """How many times each secret appears in anything captured (empty when clean)."""
    text = captured.haystack()
    lowered = text.lower()
    return {s: lowered.count(s.lower()) for s in sorted(secrets) if s.lower() in lowered}


def describe(captured: Captured) -> dict[str, Any]:
    loggers = sorted({r.name for r in captured.records})
    return {"records": len(captured.records), "loggers": loggers}
