"""Request-ID propagation (spec §101.3): one ID per request, in headers and every log line."""

import uuid
from contextvars import ContextVar

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


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()
