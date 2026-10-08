"""The NOTIFY side of live streaming: channel name and payload format (ADR-022).

The payload carries ids only (`<workspace uuid>:<run uuid>`). Streams re-read the rows
themselves under their own tenant context, so a notification can neither leak data nor be
trusted for content.
"""

import uuid

CHANNEL = "abb_run_events"

StreamKey = tuple[uuid.UUID, uuid.UUID]  # (workspace_id, run_id)


def encode(key: StreamKey) -> str:
    return f"{key[0]}:{key[1]}"


def decode(payload: str) -> StreamKey | None:
    """The key a payload names, or None for anything that is not exactly what `encode` produces."""
    workspace, sep, run = payload.partition(":")
    if not sep:
        return None
    try:
        key = (uuid.UUID(workspace), uuid.UUID(run))
    except ValueError:
        return None
    return key if encode(key) == payload else None
