"""Instrument an MCP client so tool calls become `tool.call.*` events (ADR-052).

    from blackbox_mcp import instrument
    instrument(session, bb, server="github")      # a ClientSession or the high-level Client

Only `call_tool` is wrapped. Outside a `bb.run(...)` it is a pass-through. A tool result flagged as
an error (`is_error` or `isError`) is recorded as `tool.call.failed` and still returned;
exceptions (timeouts, protocol errors) are recorded and re-raised unchanged. Arguments and results
are never captured unless `capture_payloads=True`.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from typing import Any

from blackbox import BlackBox, Span, current_run

log = logging.getLogger("blackbox.mcp")
_MARK = "_abb_wrapped"
PREVIEW_CHARS = 4096


def _get(obj: Any, key: str) -> Any:
    try:
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    except Exception:
        return None


def _preview(value: Any) -> Any:
    """A bounded JSON-safe rendering for opt-in payload capture; never raises.

    Small values stay structured so the SDK's key-based redaction (password, token, ...) applies;
    larger ones become truncated text (pattern-based redaction still applies)."""
    try:
        dump = getattr(value, "model_dump", None)
        rendered = json.dumps(
            dump() if callable(dump) else value, default=str, ensure_ascii=False, allow_nan=False
        )
    except Exception:
        return f"<{type(value).__name__}>"
    if len(rendered) <= PREVIEW_CHARS:
        return json.loads(rendered)
    return rendered[:PREVIEW_CHARS]


class _Settings:
    def __init__(self, bb: BlackBox, server: str | None, capture: bool) -> None:
        self.bb, self.server, self.capture = bb, server, capture
        self.errors = 0

    def contained(self) -> None:
        self.errors += 1
        log.debug("blackbox-mcp: bookkeeping failed and was contained", exc_info=True)


def _begin(s: _Settings, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Span | None:
    try:
        run = current_run()
        if run is None or not s.bb.enabled:
            return None
        name = args[0] if args else kwargs.get("name")
        name = name if isinstance(name, str) and name.strip() else "unknown"
        attributes: dict[str, Any] = {"framework.name": "mcp", "tool.operation": "mcp.call_tool"}
        if s.server:
            attributes["mcp.server"] = s.server
        span = run.span(name, kind="tool", attributes=attributes)
        span.start()
        return span
    except Exception:
        s.contained()
        return None


def _succeed(
    s: _Settings, span: Span, result: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    try:
        flagged = _get(result, "is_error")
        if flagged is None:
            flagged = _get(result, "isError")
        content = _get(result, "content")
        if isinstance(content, (list, tuple)):
            span.set_attribute("tool.result_count", len(content))
        if s.capture:
            arguments = args[1] if len(args) > 1 else kwargs.get("arguments")
            span.set_payload({"arguments": _preview(arguments), "result": _preview(result)})
        if flagged is True:
            span.set_attribute("tool.error_type", "ToolResultError")
            span.end("error")
        else:
            span.end("success")
    except Exception:
        s.contained()
        try:
            span.end("success")
        except Exception:
            s.contained()


def _fail(s: _Settings, span: Span, exc: BaseException) -> None:
    try:
        span.set_attribute("tool.error_type", type(exc).__name__)
        span.end("error" if isinstance(exc, Exception) else "cancelled", exc)
    except Exception:
        s.contained()


def _wrap(orig: Any, s: _Settings) -> Any:
    if inspect.iscoroutinefunction(orig):

        @functools.wraps(orig)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            span = _begin(s, args, kwargs)
            if span is None:
                return await orig(*args, **kwargs)
            try:
                result = await orig(*args, **kwargs)
            except BaseException as exc:
                _fail(s, span, exc)
                raise
            _succeed(s, span, result, args, kwargs)
            return result

        setattr(awrapper, _MARK, True)
        return awrapper

    @functools.wraps(orig)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        span = _begin(s, args, kwargs)
        if span is None:
            return orig(*args, **kwargs)
        try:
            result = orig(*args, **kwargs)
        except BaseException as exc:
            _fail(s, span, exc)
            raise
        _succeed(s, span, result, args, kwargs)
        return result

    setattr(wrapper, _MARK, True)
    return wrapper


def instrument(
    session: Any,
    bb: BlackBox | None = None,
    *,
    server: str | None = None,
    capture_payloads: bool = False,
) -> Any:
    """Instrument `session.call_tool` in place and return the session. Idempotent; never raises."""
    try:
        orig = getattr(session, "call_tool", None)
        if callable(orig) and not getattr(orig, _MARK, False):
            settings = _Settings(
                bb if isinstance(bb, BlackBox) else BlackBox(),
                server if isinstance(server, str) and server else None,
                capture_payloads is True,
            )
            session.call_tool = _wrap(orig, settings)
    except Exception:
        log.debug("blackbox-mcp: could not instrument the session", exc_info=True)
    return session
