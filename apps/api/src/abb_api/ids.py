"""Application ids. Public ids are prefixed ULIDs; the database stores their 128 bits as uuid."""

import uuid

from abb_event_schema.ids import IdKind, from_uuid, new_id, parse_id, to_uuid


def new_uuid(kind: IdKind) -> uuid.UUID:
    """A fresh, time-sortable id for a row that the API exposes with a prefix."""
    return to_uuid(new_id(kind))


def public_id(kind: IdKind, value: uuid.UUID) -> str:
    return from_uuid(kind, value)


def parse_public_id(kind: IdKind, value: str) -> uuid.UUID | None:
    """The uuid for a prefixed id of the expected kind, or None if it is malformed.

    Malformed ids are indistinguishable from unknown ones to callers (both are 404).
    """
    try:
        parse_id(value, kind)
    except ValueError:
        return None
    return to_uuid(value)
