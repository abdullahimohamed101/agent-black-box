"""Request-ID propagation (spec §101.3): one ID per request, in headers and every log line."""

import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

MAX_INBOUND_LENGTH = 64


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def sanitize_inbound(value: str | None) -> str | None:
    """Accept a caller-supplied ID only if it is short and printable; otherwise ignore it.

    Inbound IDs end up in logs, so they are untrusted input.
    """
    if not value or len(value) > MAX_INBOUND_LENGTH:
        return None
    if not all(c.isalnum() or c in "-_." for c in value):
        return None
    return value


_caller: ContextVar[dict[str, str] | None] = ContextVar("caller", default=None)


def set_caller(**identifiers: str) -> None:
    """Attach safe identifiers (workspace, project, key id) to later log lines of this request."""
    _caller.set(identifiers)


def get_caller() -> dict[str, str]:
    return _caller.get() or {}


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


# The operation is a stable short name for what the process is doing ("ingest.batch",
# "retention.tick", "job.summarize_run"). Handlers set it once; every log line of that unit of
# work carries it, so a log search by operation finds the whole story (spec §116.2).
_operation: ContextVar[str | None] = ContextVar("operation", default=None)
# The ASGI scope of the request in flight, so a log line can name the matched route template
# without any handler's help. Only the template is ever read, never the raw path.
_scope: ContextVar[Mapping[str, Any] | None] = ContextVar("scope", default=None)

_OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
MAX_OPERATION_LENGTH = 64


def is_operation_name(name: str) -> bool:
    return len(name) <= MAX_OPERATION_LENGTH and _OPERATION_PATTERN.match(name) is not None


def set_operation(name: str) -> None:
    """Name the operation for the rest of this context; the name must be a fixed identifier."""
    if not is_operation_name(name):
        raise ValueError("an operation name is a short dotted lowercase identifier")
    _operation.set(name)


@contextmanager
def operation(name: str) -> Iterator[None]:
    """Scope an operation name to a block (a job, a tick, a CLI command)."""
    if not is_operation_name(name):
        raise ValueError("an operation name is a short dotted lowercase identifier")
    token = _operation.set(name)
    try:
        yield
    finally:
        _operation.reset(token)


def set_request_scope(scope: Mapping[str, Any] | None) -> None:
    _scope.set(scope)


def route_of(scope: Mapping[str, Any] | None) -> str | None:
    """The matched route template (``/v1/runs/{run_id}``), or None when nothing matched."""
    route = (scope or {}).get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) and template else None


def get_operation() -> str | None:
    explicit = _operation.get()
    if explicit is not None:
        return explicit
    route = (_scope.get() or {}).get("route")
    name = getattr(route, "name", None)
    if isinstance(name, str) and is_operation_name(name.lower()):
        return f"http.{name.lower()}"[:MAX_OPERATION_LENGTH]
    return None
