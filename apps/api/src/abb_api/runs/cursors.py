"""Opaque keyset cursors. Tampering can only move the position inside a tenant-scoped query."""

import base64
import json
from dataclasses import dataclass
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
