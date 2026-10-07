"""Opaque keyset cursors. Tampering can only move the position inside a tenant-scoped query."""

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from abb_api.core.errors import AppError, ErrorCategory

_VERSION = 1


def cursor_invalid() -> AppError:
    return AppError(
        "CURSOR_INVALID",
        "The cursor is not valid for this endpoint.",
        category=ErrorCategory.VALIDATION,
        status_code=400,
    )


def cursor_stale() -> AppError:
    return AppError(
        "CURSOR_STALE",
        "The run's ordering changed while paging; restart from the first page.",
        category=ErrorCategory.CONFLICT,
        status_code=409,
        retryable=True,
    )


_INT64 = 2**63


def as_datetime(value: Any) -> datetime:
    """A timezone-aware ISO timestamp from a cursor, or CURSOR_INVALID. Cursors are untrusted."""
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)  # also rejects instants with no UTC form
    except (ValueError, OverflowError):
        raise cursor_invalid() from None


def as_uuid(value: Any) -> uuid.UUID:
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        return uuid.UUID(value)
    except ValueError:
        raise cursor_invalid() from None


def as_int64(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -_INT64 <= value < _INT64:
        raise cursor_invalid()  # a bool is an int in Python; it and huge numbers are not positions
    return value


@dataclass(frozen=True)
class Cursor:
    kind: str  # which listing it belongs to: "runs", "events" or "spans"
    key: list[Any]
    mode: str | None = None  # events: the ordering mode the key was built under


def encode(cursor: Cursor) -> str:
    raw = json.dumps(
        {"v": _VERSION, "k": cursor.kind, "key": cursor.key, "m": cursor.mode},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(token: str, kind: str, *, width: int) -> Cursor:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        ok = (
            isinstance(data, dict)
            and data.get("v") == _VERSION
            and data.get("k") == kind
            and isinstance(data.get("key"), list)
            and len(data["key"]) == width
        )
    except (ValueError, UnicodeError):
        raise cursor_invalid() from None
    if not ok:
        raise cursor_invalid()
    return Cursor(kind=kind, key=data["key"], mode=data.get("m"))
