"""Structured JSON logging (spec §131). Never log payload bodies or secrets."""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from abb_api.core.request_context import get_caller, get_operation, get_request_id

# Attributes every LogRecord has; anything else was passed via `extra=` and is emitted as a field.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        operation = get_operation()
        if operation is not None:
            entry["operation"] = operation
        entry.update(get_caller())
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info and record.exc_info[1] is not None:
            # The class, never the message: an exception message can quote a payload value, a
            # key or an address (a database error echoes the offending row). INV-5.
            entry.setdefault("error_type", type(record.exc_info[1]).__qualname__)
            entry["exception"] = safe_traceback(record.exc_info[1])
        return json.dumps(entry, default=str)


_MAX_FRAMES = 25
_MAX_CHAIN = 5


def safe_traceback(exc: BaseException) -> str:
    """Frames (file, line, function, source line) and exception class names; no messages.

    Source lines are our own code. Local variables are not included and the exception text is
    replaced by its class, for the exception and for everything it was chained from.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(parts) < _MAX_CHAIN and id(current) not in seen:
        seen.add(id(current))
        frames = traceback.extract_tb(current.__traceback__)[-_MAX_FRAMES:]
        text = "".join(traceback.format_list(frames))
        parts.append(f"{text}{type(current).__qualname__}\n")
        implicit = None if current.__suppress_context__ else current.__context__
        current = current.__cause__ or implicit
    return "\n".join(reversed(parts)).rstrip()


def configure_logging(level: str, service: str = "abb-api") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's own access log would duplicate (and not carry) our request-scoped fields.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs full request URLs at INFO; the identity provider calls carry codes and tokens.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # At DEBUG these log every SQL statement with its parameters (payloads, keys, addresses) and
    # every wire message. An operator asking for DEBUG must never get the data (INV-5).
    for data_bearing in ("sqlalchemy", "asyncpg", "asyncio"):
        logging.getLogger(data_bearing).setLevel(logging.WARNING)
