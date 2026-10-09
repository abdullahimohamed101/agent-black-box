"""Prefixed ULIDs (spec §64.2).

Properties we rely on: generated offline by any client, collision-free in practice, no database
sequence leakage, and time-sortable. A ULID is 128 bits, so it converts losslessly to the UUID
columns used in PostgreSQL (spec §149).
"""

import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 without I, L, O, U
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
_ULID_LENGTH = 26
_ULID_PATTERN = r"[0-7][0-9A-HJKMNP-TV-Z]{25}"  # first char <= 7: 48-bit timestamp + 80-bit entropy
_RANDOM_BITS = 80


class IdKind(str, Enum):
    EVENT = "evt"
    RUN = "run"
    TRACE = "trc"
    SPAN = "spn"
    WORKSPACE = "ws"
    PROJECT = "prj"
    ARTIFACT = "art"
    EVALUATION = "eval"
    POLICY = "pol"
    APPROVAL = "apr"
    USER = "usr"  # dashboard users (Phase 15); never appears in events
    INVITATION = "inv"


def id_pattern(kind: IdKind) -> str:
    """Regex (anchored by the caller or JSON Schema) for ids of one kind."""
    return rf"^{kind.value}_{_ULID_PATTERN}$"


_ID_RE = re.compile(rf"^(?P<prefix>[a-z]+)_(?P<ulid>{_ULID_PATTERN})$")


def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_LENGTH):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _decode(text: str) -> int:
    value = 0
    for char in text:
        value = (value << 5) | _DECODE[char]
    return value


class IdGenerator:
    """Generates ULIDs; monotonic within one millisecond so ids from one process sort in order.

    Clock and entropy are injectable so tests are deterministic.
    """

    def __init__(
        self,
        clock_ms: Callable[[], int] | None = None,
        entropy: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._entropy = entropy
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_random = 0

    def new_ulid(self) -> str:
        with self._lock:
            now = max(self._clock_ms(), self._last_ms)  # never go backwards if the clock does
            if now == self._last_ms:
                randomness = self._last_random + 1
                if randomness >= 1 << _RANDOM_BITS:  # 2**80 ids in one ms: carry into time
                    now += 1
                    randomness = int.from_bytes(self._entropy(10), "big")
            else:
                randomness = int.from_bytes(self._entropy(10), "big")
            self._last_ms, self._last_random = now, randomness
            return _encode((now << _RANDOM_BITS) | randomness)

    def new_id(self, kind: IdKind) -> str:
        return f"{kind.value}_{self.new_ulid()}"


_default_generator = IdGenerator()


def new_id(kind: IdKind) -> str:
    """Convenience for the process-wide generator."""
    return _default_generator.new_id(kind)


@dataclass(frozen=True)
class ParsedId:
    kind: IdKind
    ulid: str
    timestamp_ms: int


def parse_id(value: str, expected: IdKind | None = None) -> ParsedId:
    """Parse a prefixed ULID. Raises ValueError if malformed or of the wrong kind.

    Only the canonical (uppercase) spelling is accepted so one id has exactly one string form.
    """
    match = _ID_RE.fullmatch(value)
    if not match:
        raise ValueError("not a valid Agent Black Box id")
    try:
        kind = IdKind(match.group("prefix"))
    except ValueError:
        raise ValueError("unknown id prefix") from None
    if expected is not None and kind is not expected:
        raise ValueError(f"expected a {expected.value}_ id")
    ulid = match.group("ulid")
    return ParsedId(kind=kind, ulid=ulid, timestamp_ms=_decode(ulid) >> _RANDOM_BITS)


def to_uuid(value: str) -> uuid.UUID:
    """The 128-bit value of an id as a UUID (for PostgreSQL uuid columns)."""
    return uuid.UUID(int=_decode(parse_id(value).ulid))


def from_uuid(kind: IdKind, value: uuid.UUID) -> str:
    return f"{kind.value}_{_encode(value.int)}"
