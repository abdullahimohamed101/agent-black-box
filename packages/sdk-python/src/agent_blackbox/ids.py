"""Prefixed ULIDs (event contract: `abb_event_schema.ids`), reimplemented with the standard library.

Kept byte-compatible by contract tests (ADR-013). Monotonic within a millisecond so ids from one
process sort in creation order.
"""

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 without I, L, O, U
_RANDOM_BITS = 80
_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def reinit_lock() -> None:
    global _lock
    _lock = threading.Lock()


def _encode(value: int) -> str:
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    global _last_ms, _last_random
    with _lock:
        now = max(time.time_ns() // 1_000_000, _last_ms)  # never go backwards with the clock
        if now == _last_ms and _last_random + 1 < 1 << _RANDOM_BITS:
            randomness = _last_random + 1
        else:
            if now == _last_ms:  # 2**80 ids in one millisecond: carry into the timestamp
                now += 1
            randomness = int.from_bytes(os.urandom(10), "big")
        _last_ms, _last_random = now, randomness
        return _encode((now << _RANDOM_BITS) | randomness)


def new_id(prefix: str) -> str:
    return f"{prefix}_{new_ulid()}"


EVENT, RUN, TRACE, SPAN = "evt", "run", "trc", "spn"
