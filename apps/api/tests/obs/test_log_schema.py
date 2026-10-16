"""The structured log line (spec §116.2): fields, operation, error_type, no exception text."""

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from abb_api.core.logging import JsonFormatter, configure_logging, safe_traceback
from abb_api.core.middleware import RequestContextMiddleware
from abb_api.core.request_context import operation, set_operation

SECRET = "sentinel-in-an-exception-message-7f3a"
REQUIRED = {"timestamp", "level", "service", "logger", "message", "request_id"}


class Lines:
    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    def read(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._stream.getvalue().splitlines()]


@pytest.fixture
def lines() -> Iterator[Lines]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("abb-test"))
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    yield Lines(stream)
    root.handlers[:] = before
    root.setLevel(level)


def test_every_line_has_the_base_fields(lines: Lines) -> None:
    logging.getLogger("abb_api.test").warning("hello %s", "world")
    (entry,) = lines.read()
    assert REQUIRED <= set(entry)
    assert entry["service"] == "abb-test" and entry["level"] == "WARNING"
    assert entry["message"] == "hello world" and entry["logger"] == "abb_api.test"


def test_operation_is_scoped_and_validated() -> None:
    record = logging.LogRecord("abb_api.x", logging.INFO, __file__, 1, "m", (), None)
    formatter = JsonFormatter("svc")
    assert "operation" not in json.loads(formatter.format(record))
    with operation("retention.tick"):
        assert json.loads(formatter.format(record))["operation"] == "retention.tick"
    assert "operation" not in json.loads(formatter.format(record))
    for bad in ("Has Space", "a/b", "x" * 65, "", "UPPER"):
        with pytest.raises(ValueError):
            set_operation(bad)


def test_exception_logs_carry_the_class_never_the_message() -> None:
    try:
        try:
            raise KeyError(SECRET)
        except KeyError as inner:
            raise RuntimeError(f"wrapped {SECRET}") from inner
    except RuntimeError:
        logger = logging.getLogger("abb_api.x")
        record = logger.makeRecord(
            "abb_api.x", logging.ERROR, __file__, 1, "boom", (), __import__("sys").exc_info()
        )
    entry = json.loads(JsonFormatter("svc").format(record))
    assert entry["error_type"] == "RuntimeError"
    assert SECRET not in json.dumps(entry)
    assert "RuntimeError" in entry["exception"] and "KeyError" in entry["exception"]
    assert "test_log_schema.py" in entry["exception"]  # frames stay: the stack is the evidence


def test_safe_traceback_is_bounded() -> None:
    def deep(n: int) -> None:
        if n:
            deep(n - 1)
        raise ValueError(SECRET)

    try:
        deep(50)
    except ValueError as exc:
        text = safe_traceback(exc)
    assert SECRET not in text
    assert text.count("File ") <= 25


def test_an_explicit_error_type_wins() -> None:
    record = logging.LogRecord("abb_api.x", logging.INFO, __file__, 1, "m", (), None)
    record.error_type = "Custom"
    assert json.loads(JsonFormatter("svc").format(record))["error_type"] == "Custom"


async def test_request_line_has_template_status_operation_and_no_raw_path(
    lines: Lines,
) -> None:
    app = FastAPI()

    @app.get("/v1/runs/{run_id}")
    async def read_run(run_id: str) -> dict[str, str]:
        logging.getLogger("abb_api.handler").info("inside")
        return {}

    app.add_middleware(RequestContextMiddleware)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/v1/runs/run_SENTINELID?token=SENTINELQUERY")
        await client.get("/no/such/SENTINELPATH")
    request_id = response.headers["x-request-id"]
    # (the test client's own httpx line is not the server's)
    entries = [e for e in lines.read() if e["logger"].startswith("abb_api")]
    assert "SENTINEL" not in json.dumps(entries)
    inside = next(e for e in entries if e["message"] == "inside")
    assert inside["operation"] == "http.read_run" and inside["request_id"] == request_id
    access = [e for e in entries if e["logger"] == "abb_api.request"]
    assert [(e["route"], e["status"]) for e in access] == [
        ("/v1/runs/{run_id}", 200),
        ("unmatched", 404),
    ]
    assert access[0]["request_id"] == request_id and "path" not in access[0]


def test_configure_logging_keeps_data_bearing_libraries_quiet() -> None:
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    try:
        configure_logging("DEBUG")
        assert logging.getLogger("sqlalchemy.engine").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("asyncpg").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("urllib3").getEffectiveLevel() >= logging.ERROR
    finally:
        root.handlers[:] = before
        root.setLevel(level)
        for name in (
            "sqlalchemy",
            "asyncpg",
            "asyncio",
            "httpx",
            "httpcore",
            "urllib3",
            "requests",
        ):
            logging.getLogger(name).setLevel(logging.NOTSET)
