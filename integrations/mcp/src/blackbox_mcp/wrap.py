"""Instrument an MCP client so tool calls become `tool.call.*` events (ADR-052).

    from blackbox_mcp import instrument
    instrument(session, bb, server="github")      # a ClientSession or the high-level Client

Only `call_tool` is wrapped. Outside a `bb.run(...)` it is a pass-through. A tool result flagged as
an error (`is_error` or `isError`) is recorded as `tool.call.failed` and still returned;
exceptions (timeouts, protocol errors) are recorded and re-raised unchanged. Arguments and results
are never captured unless `capture_payloads=True`.

Policy (Phase 14): with `policy_mode="enforce"` on the client, each call inside a run first asks the
policy service as capability `tool.<name>` (unsafe characters become `_`), resource `mcp.call_tool`
(the `tool.operation` this adapter records, which is what a dry run reads), no arguments. A denied
call (or one the service cannot be asked about under a closed fail mode) is not made and no
`tool.call.*` event is recorded; the caller gets an MCP error result (`isError=True`) that says so,
the same shape a failing tool returns, and nothing is raised. Approval waits are bounded by the SDK.
The guard is cooperative: it covers calls made through the instrumented session. Outside a run there
is nothing to bind a decision to and the call passes through.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import re
from types import SimpleNamespace
from typing import Any

from blackbox import BlackBox, PolicyDenied, PolicyError, Run, Span, current_run

log = logging.getLogger("blackbox.mcp")
_MARK = "_abb_wrapped"
_UNSAFE_CAPABILITY_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
MAX_CAPABILITY_NAME = 120
POLICY_RESOURCE = "mcp.call_tool"  # the `tool.operation` this adapter records
PREVIEW_CHARS = 4096


def _get(obj: Any, key: str) -> Any:
    try:
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    except Exception:
        return None


_DELIMITERS = frozenset(" \t\r\n\"',;:[]{}()<>")


def _cut(rendered: str) -> str:
    """Truncate without leaving half a token: a secret cut in two would escape the SDK's pattern
    redaction (which runs on the truncated text) and leak its prefix."""
    head = rendered[:PREVIEW_CHARS]
    if rendered[PREVIEW_CHARS] in _DELIMITERS:
        return head
    for i in range(len(head) - 1, -1, -1):
        if i > 0 and head[i] in _DELIMITERS:
            return head[:i]
    return f"<{len(rendered)} characters>"


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
    return _cut(rendered)


class _Settings:
    def __init__(self, bb: BlackBox, server: str | None, capture: bool) -> None:
        self.bb, self.server, self.capture = bb, server, capture
        self.errors = 0
        self.late_dropped = 0

    def contained(self) -> None:
        self.errors += 1
        log.debug("blackbox-mcp: bookkeeping failed and was contained", exc_info=True)


def _name_of(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return args[0] if args else kwargs.get("name")


def _begin(s: _Settings, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Span | None:
    try:
        run = current_run()
        if run is None or run.ended or not s.bb.enabled:
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


def _late(s: _Settings, span: Span) -> bool:
    """True (and counted) when the run already ended: nothing may follow a run's end event."""
    if getattr(span.run, "ended", False):
        s.late_dropped += 1
        return True
    return False


def _succeed(
    s: _Settings, span: Span, result: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    if _late(s, span):
        return
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
    if _late(s, span):
        return
    try:
        span.set_attribute("tool.error_type", type(exc).__name__)
        span.end("error" if isinstance(exc, Exception) else "cancelled", exc)
    except Exception:
        s.contained()


def _capability(name: Any) -> str:
    """`tool.<name>` as the dry-run mapping builds it from the recorded `tool.name`."""
    text = name if isinstance(name, str) and name.strip() else "unknown"
    return "tool." + _UNSAFE_CAPABILITY_CHARS.sub("_", text)[:MAX_CAPABILITY_NAME].strip(".")


def _policy_run(s: _Settings) -> Run | None:
    """The run to ask about, or None when calls are not guarded (policy off, no run)."""
    try:
        run = current_run()
        if run is None or run.ended or s.bb.config.policy_mode != "enforce":
            return None
        return run
    except Exception:
        s.contained()
        return None


def _blocked(error: PolicyError) -> Any:
    """An MCP error result: what a failing tool returns, so hosts and models handle it as usual."""
    if isinstance(error, PolicyDenied):
        rule = f"{error.policy_id or 'default'} v{error.policy_version}"
        text = f"Blocked by policy ({rule}): {error.reason}"
    else:
        text = "Blocked: the policy service could not be asked and the fail mode is closed."
    text = text[:500]
    try:
        from mcp.types import CallToolResult

        # Validated from the wire shape so it holds across mcp versions (`isError` / `is_error`).
        return CallToolResult.model_validate(
            {"content": [{"type": "text", "text": text}], "isError": True}
        )
    except Exception:  # the mcp package is optional (ADR-050)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], is_error=True, isError=True
        )


def _wrap(orig: Any, s: _Settings) -> Any:
    if inspect.iscoroutinefunction(orig):

        @functools.wraps(orig)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            run = _policy_run(s)
            if run is not None:
                try:
                    await s.bb.aguard(_capability(_name_of(args, kwargs)), POLICY_RESOURCE, run=run)
                except PolicyError as error:
                    return _blocked(error)
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
        run = _policy_run(s)
        if run is not None:
            try:
                s.bb.guard(_capability(_name_of(args, kwargs)), POLICY_RESOURCE, run=run)
            except PolicyError as error:
                return _blocked(error)
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
