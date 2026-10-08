"""Defensive readers for LangChain callback arguments (every input is untrusted)."""

import json
from typing import Any

PREVIEW_CHARS = 4096
_UNKNOWN = "unknown"


def _get(obj: Any, key: str) -> Any:
    """`obj[key]` or `obj.key`, None when absent or when the lookup itself fails."""
    try:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    except Exception:
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and value >= 0 else None


def span_name(serialized: Any, kwargs: dict[str, Any], default: str) -> str:
    name = _text(kwargs.get("name")) or _text(_get(serialized, "name"))
    if name:
        return name
    ident = _get(serialized, "id")
    if isinstance(ident, (list, tuple)) and ident and isinstance(ident[-1], str):
        return ident[-1] or default
    return default


def run_name(kwargs: dict[str, Any], fallback: str) -> str:
    return _text(kwargs.get("name")) or fallback


def tool_name(serialized: Any, kwargs: dict[str, Any]) -> str:
    return span_name(serialized, kwargs, "tool")


def llm_identity(serialized: Any, metadata: Any, kwargs: dict[str, Any]) -> tuple[str, str]:
    """(provider, model) from the best available hint; "unknown" when the framework gave none."""
    params = kwargs.get("invocation_params")
    provider = _text(_get(metadata, "ls_provider")) or _text(_get(params, "_type"))
    model = (
        _text(_get(metadata, "ls_model_name"))
        or _text(_get(params, "model"))
        or _text(_get(params, "model_name"))
        or _text(_get(_get(serialized, "kwargs"), "model_name"))
        or _text(_get(_get(serialized, "kwargs"), "model"))
    )
    if provider is None:
        ident = _get(serialized, "id")
        if isinstance(ident, (list, tuple)) and ident and isinstance(ident[-1], str):
            provider = ident[-1]
    return provider or _UNKNOWN, model or _UNKNOWN


def llm_options(metadata: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    temperature = _get(metadata, "ls_temperature")
    if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
        if temperature >= 0:
            out["temperature"] = float(temperature)
    max_tokens = _count(_get(metadata, "ls_max_tokens"))
    if max_tokens is not None:
        out["max_tokens"] = max_tokens
    return out


def _from_usage(usage: Any) -> tuple[int | None, int | None, int | None]:
    tokens_in = _count(_get(usage, "input_tokens"))
    if tokens_in is None:
        tokens_in = _count(_get(usage, "prompt_tokens"))
    tokens_out = _count(_get(usage, "output_tokens"))
    if tokens_out is None:
        tokens_out = _count(_get(usage, "completion_tokens"))
    cached = _count(_get(_get(usage, "input_token_details"), "cache_read"))
    if cached is None:
        cached = _count(_get(_get(usage, "prompt_tokens_details"), "cached_tokens"))
    # Raw Anthropic usage reports cache reads and writes apart from `input_tokens`; the canonical
    # input is the whole prompt (same as integrations/anthropic).
    read = _count(_get(usage, "cache_read_input_tokens"))
    created = _count(_get(usage, "cache_creation_input_tokens"))
    if read is not None or created is not None:
        tokens_in = (tokens_in or 0) + (read or 0) + (created or 0)
        cached = read if read is not None else cached
    return tokens_in, tokens_out, cached


def usage_of(response: Any) -> tuple[int | None, int | None, int | None]:
    """(input, output, cached input) tokens of an `LLMResult`.

    The normalised `message.usage_metadata` wins; the provider's raw `llm_output` is the fallback."""
    totals: list[int | None] = [None, None, None]
    generations = _get(response, "generations")
    if isinstance(generations, (list, tuple)):
        for group in generations[:64]:
            for generation in (group if isinstance(group, (list, tuple)) else [])[:64]:
                usage = _get(_get(generation, "message"), "usage_metadata")
                for i, value in enumerate(_from_usage(usage)):
                    if value is not None:
                        totals[i] = (totals[i] or 0) + value
    if totals[0] is not None or totals[1] is not None:
        return totals[0], totals[1], totals[2]
    output = _get(response, "llm_output")
    for key in ("token_usage", "usage"):
        found = _from_usage(_get(output, key))
        if found[0] is not None or found[1] is not None:
            return found
    return None, None, None


def preview(value: Any) -> Any:
    """A bounded JSON-safe rendering for opt-in payload capture; never raises.

    Small values stay structured so the SDK's key-based redaction (password, token, ...) applies;
    larger ones become truncated text (pattern-based redaction still applies)."""
    try:
        rendered = json.dumps(value, default=str, ensure_ascii=False, allow_nan=False)
    except Exception:
        return f"<{type(value).__name__}>"
    if len(rendered) <= PREVIEW_CHARS:
        return json.loads(rendered)
    return rendered[:PREVIEW_CHARS]
