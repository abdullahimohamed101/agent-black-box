"""The action intent the SDK sends to the policy service, checked before it leaves the process.

The server refuses what it cannot canonicalise (ADR-141 point 1: depth 8, 512 values, strings
4,096 characters, keys 256, resource 2,048, capability 128, 24 KiB canonical). Checking here means
an oversized or malformed intent is never sent: `guard()` follows the fail mode with a fixed reason
code instead. The check is a port of the canonical form (not of the hash: the SDK never computes
or sees a hash), and a test runs it against the shared golden vectors.

Floats are converted to strings (the server refuses floats; there is no portable shortest-float
form), tuples to lists. Anything else that is not JSON is `invalid_intent`.
"""

import re
import unicodedata
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1
MAX_DEPTH = 8
MAX_NODES = 512
MAX_STRING_CHARS = 4096
MAX_KEY_CHARS = 256
MAX_CAPABILITY_CHARS = 128
MAX_RESOURCE_CHARS = 2048
MAX_CANONICAL_BYTES = 24 * 1024
MAX_ATTRIBUTES = 64  # top-level scalar arguments a rule can match; more is refused, never cut
RISK_CLASSES = ("R0", "R1", "R2", "R3", "R4")
CAPABILITY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,5}$")
# Stand-ins with the length of the real ids (a 3 or 4 letter prefix and a 26 character ULID), so
# the size check counts what the server will count.
_WORKSPACE_ID = "ws_" + "0" * 26
_PROJECT_ID = "prj_" + "0" * 26

REASON_INVALID = "invalid_intent"
REASON_TOO_LARGE = "intent_too_large"
_TOO_LARGE_CODES = frozenset(
    {"string_too_long", "too_many_values", "too_deep", "intent_too_large", "key_too_long"}
)

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class IntentRejected(ValueError):
    """The intent cannot be sent. `code` is a fixed word and never quotes the input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    @property
    def reason(self) -> str:
        """The `policy.reason` vocabulary word for this rejection."""
        return REASON_TOO_LARGE if self.code in _TOO_LARGE_CODES else REASON_INVALID


def _text(value: str, limit: int, code: str = "string_too_long") -> str:
    if len(value) > limit:
        raise IntentRejected(code)
    if "\x00" in value:
        raise IntentRejected("string_has_nul")
    normal = unicodedata.normalize("NFC", value)
    try:
        normal.encode("utf-8")
    except UnicodeEncodeError:
        raise IntentRejected("string_not_unicode") from None
    return normal


def _quote(normal: str) -> str:
    out = ['"']
    for char in normal:
        short = _SHORT_ESCAPES.get(char)
        if short is not None:
            out.append(short)
        elif char < " ":
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _clean(value: Any, depth: int, budget: list[int]) -> Any:
    """A JSON-safe copy with floats as strings; raises IntentRejected. Bounded while walking."""
    budget[0] -= 1
    if budget[0] < 0:
        raise IntentRejected("too_many_values")
    if depth > MAX_DEPTH:
        raise IntentRejected("too_deep")
    if value is None or value is True or value is False:
        return value
    if isinstance(value, int):
        number = int(value)  # an IntEnum or other subclass becomes a plain int
        if abs(number) > MAX_SAFE_INTEGER:
            raise IntentRejected("integer_out_of_range")
        return number
    if isinstance(value, float):
        return repr(float(value))  # 1.5 -> "1.5", nan -> "nan": the server refuses floats
    if isinstance(value, str):
        return _text(str.__str__(value), MAX_STRING_CHARS)
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1, budget) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise IntentRejected("key_not_string")
            plain = str.__str__(key)
            _text(plain, MAX_KEY_CHARS, "key_too_long")
            if plain in out:
                raise IntentRejected("duplicate_key")
            out[plain] = _clean(item, depth + 1, budget)
        return out
    raise IntentRejected("type_not_allowed")


def _canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _quote(unicodedata.normalize("NFC", value))
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    members: dict[str, Any] = {}
    for key, item in value.items():
        normal = unicodedata.normalize("NFC", key)
        if normal in members:
            raise IntentRejected("duplicate_key")
        members[normal] = item
    return "{" + ",".join(f"{_quote(k)}:{_canonical(members[k])}" for k in sorted(members)) + "}"


def canonical_text(
    *,
    workspace_id: str,
    project_id: str,
    run_id: str,
    agent_id: str,
    capability: str,
    resource: str,
    risk_class: str | None,
    arguments: dict[str, Any],
) -> str:
    """The canonical JSON of an intent (ADR-141), for the size check and the golden-vector test."""
    fields = {
        "agent_id": _quote(unicodedata.normalize("NFC", agent_id)),
        "arguments": _canonical(arguments),
        "capability": _quote(unicodedata.normalize("NFC", capability)),
        "project_id": _quote(project_id),
        "resource": _quote(unicodedata.normalize("NFC", resource)),
        "risk_class": "null" if risk_class is None else _quote(risk_class),
        "run_id": _quote(run_id),
        "workspace_id": _quote(workspace_id),
    }
    return "{" + ",".join(f'"{key}":{fields[key]}' for key in sorted(fields)) + "}"


def build_intent(
    *,
    run_id: str,
    agent_id: str,
    capability: Any,
    resource: Any,
    risk_class: Any,
    arguments: Any,
) -> dict[str, Any]:
    """The intent body for `/v1/policy/decide` and `/consume`, or IntentRejected.

    The returned dict has exactly the six fields the server hashes (plus nothing else); the caller
    adds `trace_id` and `idempotency_key` to the decide request only.
    """
    if not isinstance(capability, str) or len(capability) > MAX_CAPABILITY_CHARS:
        raise IntentRejected("capability_invalid")
    if not CAPABILITY_RE.fullmatch(capability):
        raise IntentRejected("capability_invalid")
    if resource is None:
        resource = ""
    if not isinstance(resource, str):
        raise IntentRejected("type_not_allowed")
    resource = _text(str.__str__(resource), MAX_RESOURCE_CHARS)
    if risk_class is not None and risk_class not in RISK_CLASSES:
        raise IntentRejected("risk_class_invalid")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise IntentRejected("arguments_not_object")
    cleaned = _clean(arguments, 0, [MAX_NODES])
    # The server matches rules against every top-level scalar and refuses an intent with more
    # than it will look at (review P2-2): the SDK refuses the same way, before sending.
    if sum(1 for v in cleaned.values() if isinstance(v, (bool, int, str))) > MAX_ATTRIBUTES:
        raise IntentRejected("too_many_attributes")
    intent = {
        "run_id": run_id,
        "agent_id": agent_id,
        "capability": capability,
        "resource": resource,
        "risk_class": risk_class,
        "arguments": cleaned,
    }
    size = len(
        canonical_text(
            workspace_id=_WORKSPACE_ID,
            project_id=_PROJECT_ID,
            run_id=run_id,
            agent_id=agent_id,
            capability=capability,
            resource=resource,
            risk_class=risk_class,
            arguments=cleaned,
        ).encode("utf-8")
    )
    if size > MAX_CANONICAL_BYTES:
        raise IntentRejected("intent_too_large")
    return intent
