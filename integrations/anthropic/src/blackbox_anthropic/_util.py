"""Defensive readers for client arguments and responses (everything is untrusted)."""

import json
from typing import Any

PREVIEW_CHARS = 4096


def get(obj: Any, key: str) -> Any:
    """`obj[key]` or `obj.key`; None when absent or when the lookup itself fails."""
    try:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    except Exception:
        return None


def text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and value >= 0 else None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


def preview(value: Any) -> str:
    """Bounded text for opt-in payload capture; never raises."""
    try:
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            value = dump()
        rendered = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        rendered = f"<{type(value).__name__}>"
    return rendered[:PREVIEW_CHARS]
