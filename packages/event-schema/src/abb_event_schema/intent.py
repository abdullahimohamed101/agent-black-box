"""The action-intent hash that binds an approval to exactly one action (Phase 14, ADR-141).

An approval authorizes one intent once. The intent is `{workspace_id, project_id, run_id,
agent_id, capability, resource, risk_class, arguments}` as the SDK sent it, before any
scrubbing, so a different command can never produce the same hash. This module is the reference
implementation of the canonical form and the hash; the SDK reimplements it and
`tests/data/intent-hash-vectors.json` is the shared proof that both agree, byte for byte.

Canonical form (version 1), written down so any language can reproduce it:

* The document is one JSON object with exactly these eight keys, in this order (which is also
  code point order): `agent_id`, `arguments`, `capability`, `project_id`, `resource`,
  `risk_class`, `run_id`, `workspace_id`. `risk_class` is a string or `null`; `resource` is a
  string (empty when the action has none); `arguments` is a JSON object (empty when none).
* No whitespace anywhere. Object keys are sorted by Unicode code point (not UTF-16 code unit,
  which differs above U+FFFF), at every depth.
* Every string, keys included, is normalised to Unicode NFC first. Two keys that become equal
  after normalisation are an error, never silently merged.
* Strings are written as UTF-8 with only these escapes: `\\"`, `\\\\`, `\\b`, `\\f`, `\\n`, `\\r`,
  `\\t`, and `\\u00xx` (lower case hex) for any other code point below U+0020. Everything else,
  including U+007F and non-ASCII text, is written raw. U+0000 and lone surrogates are errors.
* Numbers are integers only, written in decimal with no sign for non-negatives, no leading
  zeros and no exponent, and must satisfy |n| <= 2**53 - 1. Floats are errors (send a decimal
  string instead): there is no portable shortest-float form to agree on. `true`, `false` and
  `null` are allowed. Nothing else is (no tuples, bytes, NaN).
* The hash is SHA-256 over `b"abb.intent.v1\\n"` followed by the canonical UTF-8 bytes, written
  as 64 lower-case hex digits. The prefix separates this hash from any other use of SHA-256.

Bounds (checked while walking, so a hostile document cannot cost more than the bound): arguments
nest at most 8 deep, hold at most 512 values, have strings of at most 4,096 characters and keys of
at most 256, and the canonical bytes of the whole intent stay under 24 KiB. Errors carry a fixed
`code` and never the value that caused them.
"""

import hashlib
import unicodedata
from typing import Any

DOMAIN = b"abb.intent.v1\n"
MAX_SAFE_INTEGER = 2**53 - 1
MAX_DEPTH = 8
MAX_NODES = 512
MAX_STRING_CHARS = 4096
MAX_KEY_CHARS = 256
MAX_CAPABILITY_CHARS = 128
MAX_RESOURCE_CHARS = 2048
MAX_CANONICAL_BYTES = 24 * 1024

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class IntentError(ValueError):
    """The intent cannot be canonicalised. `code` is a fixed word; nothing quotes the input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value: str, limit: int) -> str:
    if len(value) > limit:
        raise IntentError("string_too_long")
    if "\x00" in value:
        raise IntentError("string_has_nul")
    normal = unicodedata.normalize("NFC", value)
    try:
        normal.encode("utf-8")
    except UnicodeEncodeError:
        raise IntentError("string_not_unicode") from None
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


def _walk(value: Any, depth: int, budget: list[int]) -> str:
    budget[0] -= 1
    if budget[0] < 0:
        raise IntentError("too_many_values")
    if depth > MAX_DEPTH:
        raise IntentError("too_deep")
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > MAX_SAFE_INTEGER:
            raise IntentError("integer_out_of_range")
        return str(value)
    if isinstance(value, float):
        raise IntentError("float_not_allowed")
    if isinstance(value, str):
        return _quote(_text(value, MAX_STRING_CHARS))
    if isinstance(value, list):
        return "[" + ",".join(_walk(item, depth + 1, budget) for item in value) + "]"
    if isinstance(value, dict):
        members: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise IntentError("key_not_string")
            normal = _text(key, MAX_KEY_CHARS)
            if normal in members:
                raise IntentError("duplicate_key")
            members[normal] = item
        return (
            "{"
            + ",".join(
                f"{_quote(key)}:{_walk(members[key], depth + 1, budget)}"
                for key in sorted(members)  # code point order
            )
            + "}"
        )
    raise IntentError("type_not_allowed")


def canonical_intent(
    *,
    workspace_id: str,
    project_id: str,
    run_id: str,
    agent_id: str,
    capability: str,
    resource: str,
    risk_class: str | None,
    arguments: dict[str, Any],
) -> bytes:
    """The canonical UTF-8 bytes of an intent (the hash input without its domain prefix)."""
    if not isinstance(arguments, dict):
        raise IntentError("arguments_not_object")
    if risk_class is not None and not isinstance(risk_class, str):
        raise IntentError("type_not_allowed")
    budget = [MAX_NODES]
    fields = {
        "agent_id": _quote(_text(agent_id, 64)),
        "arguments": _walk(arguments, 0, budget),
        "capability": _quote(_text(capability, MAX_CAPABILITY_CHARS)),
        "project_id": _quote(_text(project_id, 64)),
        "resource": _quote(_text(resource, MAX_RESOURCE_CHARS)),
        "risk_class": "null" if risk_class is None else _quote(_text(risk_class, 8)),
        "run_id": _quote(_text(run_id, 64)),
        "workspace_id": _quote(_text(workspace_id, 64)),
    }
    document = "{" + ",".join(f'"{key}":{fields[key]}' for key in sorted(fields)) + "}"
    encoded = document.encode("utf-8")
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise IntentError("intent_too_large")
    return encoded


def intent_hash(
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
    """SHA-256 (64 lower-case hex digits) of the domain-separated canonical intent."""
    canonical = canonical_intent(
        workspace_id=workspace_id,
        project_id=project_id,
        run_id=run_id,
        agent_id=agent_id,
        capability=capability,
        resource=resource,
        risk_class=risk_class,
        arguments=arguments,
    )
    return hashlib.sha256(DOMAIN + canonical).hexdigest()
