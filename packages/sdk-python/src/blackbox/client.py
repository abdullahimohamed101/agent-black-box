"""Public API (spec §67-68): BlackBox, Run, Span, LlmCall, observe.

INV-4: nothing here raises into the host application and nothing blocks on I/O. Exceptions raised
by the application inside `with` blocks are recorded and always propagate unchanged.
"""

import atexit
import contextvars
import functools
import inspect
import itertools
import logging
import os
import threading
import time
import weakref
from collections.abc import Callable
from types import TracebackType
from typing import Any, TypeVar, cast, overload

from blackbox import ids
from blackbox.buffer import EventBuffer
from blackbox.config import Config, agent_slug
from blackbox.context import _current_run, _current_span
from blackbox.events import (
    EVENT_TYPE_RE,
    build_event,
    check_payload,
    clean_attributes,
    clean_tags,
    name_attr,
    priority_of,
    span_kind,
)
from blackbox.exporter import Exporter, HttpSink, LocalSink, Sink
from blackbox.redaction import Redactor
from blackbox.stats import Stats
from blackbox.version import __version__

log = logging.getLogger("blackbox")
F = TypeVar("F", bound=Callable[..., Any])
_S = TypeVar("_S", bound="Span")

_SUCCESS, _ERROR, _CANCELLED = "success", "error", "cancelled"
# kind -> (started, completed, failed) event types; other kinds use the generic span events.
_TYPED_SPANS = {
    "tool": ("tool.call.started", "tool.call.completed", "tool.call.failed"),
    "agent": ("agent.started", "agent.completed", "agent.completed"),
}
_GENERIC_SPAN = ("span.started", "span.completed", "span.failed")
_RUN_END = {  # requested status -> (event type, event status)
    "success": ("run.completed", "success"),
    "error": ("run.failed", "error"),
    "timeout": ("run.completed", "timeout"),
    "blocked": ("run.completed", "blocked"),
    "cancelled": ("run.cancelled", "cancelled"),
}


def _outcome(exc: BaseException | None) -> str:
    """Map an exception leaving a `with` block to a status."""
    if exc is None:
        return _SUCCESS
    if isinstance(exc, SystemExit) and exc.code in (0, None):
        return _SUCCESS
    return _ERROR if isinstance(exc, Exception) else _CANCELLED  # KeyboardInterrupt, CancelledError


def _error_attributes(exc: BaseException | None) -> dict[str, Any]:
    if exc is None:
        return {}
    try:
        return {"error.type": type(exc).__name__, "error.message": str(exc)[:500]}
    except Exception:
        return {"error.type": type(exc).__name__}


class Span:
    """A timed unit of work inside a run. Use as a context manager."""

    def __init__(
        self,
        run: "Run",
        name: str,
        kind: str = "custom",
        attributes: dict[str, Any] | None = None,
        parent: "Span | None" = None,
    ) -> None:
        self.run = run
        self.name = name_attr(name)
        self.kind = span_kind(kind)
        self.span_id = ids.new_id(ids.SPAN)
        self._parent = parent
        self._types = _TYPED_SPANS.get(self.kind, _GENERIC_SPAN)
        self._base = self._base_attributes()
        self._attrs: dict[str, Any] = dict(attributes or {})
        self._payload: dict[str, Any] | None = None
        self._t0 = 0.0
        self._started = False
        self._ended = False
        self._lock = threading.Lock()
        self._token: contextvars.Token[Span | None] | None = None

    def _base_attributes(self) -> dict[str, Any]:
        if self.kind == "tool":
            return {"tool.name": self.name}
        return {"span.name": self.name, "span.kind": self.kind}

    # -- recording ---------------------------------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> None:
        self._attrs[key] = value

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self._attrs.update(attributes)

    def set_payload(self, payload: dict[str, Any]) -> None:
        """Attach a payload to the closing event. Only sent in `PayloadMode.FULL`."""
        self._payload = payload

    def event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None = None,
        *,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        """A point event inside this span."""
        self.run._event(event_type, attributes, payload, status, span_id=self.span_id)

    # -- lifecycle ---------------------------------------------------------------------------------

    def start(self: _S) -> _S:
        with self._lock:
            if self._started:
                return self
            self._started = True
        self._t0 = time.monotonic()
        if self._parent is None:
            current = _current_span.get()
            self._parent = current if current is not None and current.run is self.run else None
        self.run._emit(
            self._types[0],
            span_id=self.span_id,
            parent_span_id=self._parent.span_id if self._parent else None,
            attributes={**self._attrs, **self._base},
        )
        self._attrs = {}  # attributes given at start are on the started event only
        return self

    def end(self, status: str = _SUCCESS, exc: BaseException | None = None) -> None:
        if not self._started:
            self.start()
        with self._lock:
            if self._ended:
                return
            self._ended = True
        duration = (time.monotonic() - self._t0) * 1000
        attributes = {**self._attrs, **self._base, **_error_attributes(exc)}
        if self.kind == "tool":
            attributes["tool.latency_ms"] = round(duration, 3)
        failed = status == _ERROR
        self.run._emit(
            self._types[2] if failed else self._types[1],
            span_id=self.span_id,
            parent_span_id=self._parent.span_id if self._parent else None,
            status=status,
            duration_ms=duration,
            attributes=attributes,
            payload=self._payload,
        )

    def __enter__(self: _S) -> _S:
        self.start()
        self._token = _current_span.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._reset_context()
        self.end(_outcome(exc), exc)

    async def __aenter__(self: _S) -> _S:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)

    def _reset_context(self) -> None:
        token, self._token = self._token, None
        if token is not None:
            try:
                _current_span.reset(token)
            except ValueError:  # exited in a different context than it was entered in
                _current_span.set(None)


class LlmCall(Span):
    """A model call. `record_usage` fills token counts; the cost is yours to provide (Phase 7)."""

    def __init__(
        self,
        run: "Run",
        provider: str,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.provider, self.model = name_attr(provider), name_attr(model)
        given = dict(attributes or {})
        if temperature is not None:
            given["llm.temperature"] = temperature
        if max_tokens is not None:
            given["llm.max_tokens"] = max_tokens
        super().__init__(run, f"{self.provider}/{self.model}", "llm", given)
        self._types = ("llm.request.started", "llm.request.completed", "llm.request.failed")

    def _base_attributes(self) -> dict[str, Any]:
        return {"llm.provider": self.provider, "llm.model": self.model}

    def record_usage(
        self,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        *,
        cached_input_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        for key, value in (
            ("llm.input_tokens", input_tokens),
            ("llm.output_tokens", output_tokens),
            ("llm.cached_input_tokens", cached_input_tokens),
        ):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self._attrs[key] = value
        if isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool) and cost_usd >= 0:
            self._attrs["cost.estimated_usd"] = float(cost_usd)

    def end(self, status: str = _SUCCESS, exc: BaseException | None = None) -> None:
        if self._started and not self._ended:
            self._attrs.setdefault("llm.latency_ms", round((time.monotonic() - self._t0) * 1000, 3))
        super().end(status, exc)


class Run:
    """One execution of an agent. Created by `BlackBox.run`; use as a context manager."""

    def __init__(
        self,
        bb: "BlackBox",
        name: str | None,
        metadata: dict[str, Any] | None,
        agent_id: str | None,
        tags: tuple[str, ...],
    ) -> None:
        self._bb = bb
        self.name = name
        self.run_id = ids.new_id(ids.RUN)
        self.trace_id = ids.new_id(ids.TRACE)
        self.agent_id = agent_slug(agent_id, bb.config.agent_id) if agent_id else bb.config.agent_id
        self._tags = clean_tags(bb.config.tags + tags)
        self._seq = itertools.count(1)  # next() on a C counter is atomic: no lock on the hot path
        self._t0 = time.monotonic()
        self._ended = False
        self._lock = threading.Lock()
        self._token: contextvars.Token[Run | None] | None = None
        self._span_token: contextvars.Token[Span | None] | None = None
        attributes: dict[str, Any] = {}
        if name:
            attributes["run.name"] = name_attr(name)
        for key, value in (metadata or {}).items():
            attributes[f"metadata.{str(key).lower()}"] = value
        self._emit("run.started", attributes=attributes)

    # -- instrumentation ---------------------------------------------------------------------------

    def span(
        self,
        name: str,
        kind: str = "custom",
        attributes: dict[str, Any] | None = None,
        *,
        parent: Span | None = None,
    ) -> Span:
        return Span(self, name, kind, attributes, parent)

    def llm_call(
        self,
        provider: str,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> LlmCall:
        return LlmCall(
            self,
            provider,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            attributes=attributes,
        )

    def event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None = None,
        *,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        """Record a point event (inside the current span of this run, if any)."""
        span = _current_span.get()
        span_id = span.span_id if span is not None and span.run is self else None
        self._event(event_type, attributes, payload, status, span_id=span_id)

    def _event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None,
        payload: dict[str, Any] | None,
        status: str | None,
        *,
        span_id: str | None,
    ) -> None:
        if not isinstance(event_type, str):
            event_type = ""
        if "." not in event_type and event_type:
            event_type = f"custom.{event_type}"
        if not EVENT_TYPE_RE.match(event_type) or len(event_type) > 64:
            self._bb.stats_.add("dropped_invalid")
            return
        self._emit(
            event_type, span_id=span_id, attributes=attributes, payload=payload, status=status
        )

    def end(self, status: str = _SUCCESS, exc: BaseException | None = None) -> None:
        """Finish the run. Idempotent; `status` is success, error, timeout, blocked or cancelled."""
        with self._lock:
            if self._ended:
                return
            self._ended = True
        event_type, event_status = _RUN_END.get(status, _RUN_END[_SUCCESS])
        self._emit(
            event_type,
            status=event_status,
            duration_ms=(time.monotonic() - self._t0) * 1000,
            attributes=_error_attributes(exc),
        )

    # -- internals ---------------------------------------------------------------------------------

    def _emit(
        self,
        event_type: str,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        attributes: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._bb._emit(
            self,
            event_type,
            next(self._seq),
            span_id,
            parent_span_id,
            status,
            duration_ms,
            attributes,
            payload,
        )

    def __enter__(self) -> "Run":
        self._token = _current_run.set(self)
        self._span_token = _current_span.set(None)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        span_token, run_token = self._span_token, self._token
        self._token = self._span_token = None
        if span_token is not None:
            try:
                _current_span.reset(span_token)
            except ValueError:  # exited in a different context than it was entered in
                _current_span.set(None)
        if run_token is not None:
            try:
                _current_run.reset(run_token)
            except ValueError:
                _current_run.set(None)
        self.end(_outcome(exc), exc)

    async def __aenter__(self) -> "Run":
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


_INSTANCES: "weakref.WeakSet[BlackBox]" = weakref.WeakSet()


def _reinit_after_fork() -> None:
    ids.reinit_lock()
    for bb in list(_INSTANCES):
        bb._after_fork()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reinit_after_fork)


def _atexit_shutdown(ref: "weakref.ReferenceType[BlackBox]") -> None:
    bb = ref()
    if bb is not None:
        bb.shutdown()


class BlackBox:
    """Entry point. Cheap to create; the exporter thread starts with the first event."""

    def __init__(
        self,
        api_key: str | None = None,
        project: str | None = None,
        endpoint: str | None = None,
        **options: Any,
    ) -> None:
        self.stats_ = Stats()
        try:
            self.config = Config.build(
                api_key=api_key, project=project, endpoint=endpoint, **options
            )
        except Exception:  # a bug in option handling must not break the host (INV-4)
            log.exception("blackbox: invalid configuration; telemetry disabled")
            self.config = Config(mode="disabled")
        self._enabled = self.config.mode != "disabled"
        self._redactor = Redactor(
            payload_mode=self.config.payload_mode,
            deny_keys=self.config.deny_keys,
            allow_keys=self.config.allow_keys,
            callback=self.config.redactor,
            stats=self.stats_,
        )
        self._buffer = EventBuffer(self.config.max_queue, self.stats_)
        self._exporter = self._new_exporter()
        self._start_lock = threading.Lock()
        self._started = False
        self._closed = False
        if self._enabled:
            atexit.register(_atexit_shutdown, weakref.ref(self))
            _INSTANCES.add(self)

    # -- public API --------------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._closed

    def run(
        self,
        name: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        agent_id: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> Run:
        """Start a run (`run.started` is recorded now). Use it as `with bb.run(...) as run:`."""
        return Run(self, name, metadata, agent_id, tags)

    start_run = run

    def event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None = None,
        *,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        """A point event in the current run (ignored when there is none)."""
        current = _current_run.get()
        if current is not None:
            current.event(event_type, attributes, payload=payload, status=status)

    @overload
    def observe(self, fn: F) -> F: ...

    @overload
    def observe(self, *, kind: str = ..., name: str | None = ...) -> Callable[[F], F]: ...

    def observe(
        self, fn: F | None = None, *, kind: str = "custom", name: str | None = None
    ) -> F | Callable[[F], F]:
        """Decorator: wrap a function (sync or async) in a span of the current run.

        Outside a run the function simply runs untraced. Arguments and results are not captured.
        """

        def decorate(func: F) -> F:
            label = str(name or getattr(func, "__qualname__", "function"))

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def awrapper(*args: Any, **kwargs: Any) -> Any:
                    run = _current_run.get()
                    if run is None:
                        return await func(*args, **kwargs)
                    with run.span(label, kind=kind):
                        return await func(*args, **kwargs)

                return cast(F, awrapper)

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                run = _current_run.get()
                if run is None:
                    return func(*args, **kwargs)
                with run.span(label, kind=kind):
                    return func(*args, **kwargs)

            return cast(F, wrapper)

        return decorate(fn) if fn is not None else decorate

    def flush(self, timeout: float | None = None) -> bool:
        """Wait (bounded) until queued events were handed to the sink. Never raises."""
        try:
            if not self._started:
                return len(self._buffer) == 0
            return self._exporter.flush(
                self.config.shutdown_timeout if timeout is None else timeout
            )
        except Exception:
            self.stats_.add("internal_errors")
            return False

    def shutdown(self, timeout: float | None = None) -> bool:
        """Flush and stop the exporter. Idempotent; bounded by `timeout`; never raises."""
        if self._closed:
            return True
        self._closed = True
        try:
            return self._exporter.shutdown(
                self.config.shutdown_timeout if timeout is None else timeout
            )
        except Exception:
            self.stats_.add("internal_errors")
            return False

    def stats(self) -> dict[str, int]:
        """Local counters, including everything that was dropped and why."""
        snapshot = self.stats_.snapshot()
        snapshot["queue_size"] = len(self._buffer)
        return snapshot

    def buffered_events(self) -> list[dict[str, Any]]:
        """Offline mode: take the queued events (e.g. to inspect or ship them yourself)."""
        return self._buffer.take(len(self._buffer))

    # -- internals ---------------------------------------------------------------------------------

    def _new_exporter(self) -> Exporter:
        sink: Sink | None = None
        try:
            if self.config.mode == "http":
                sink = HttpSink(self.config, self.stats_)
            elif self.config.mode == "local" and self.config.local_path:
                sink = LocalSink(self.config.local_path, self.stats_)
        except Exception:  # INV-4: a bad option must not break the host
            log.exception("blackbox: could not create the exporter; events stay local")
            self.stats_.add("internal_errors")
        return Exporter(self.config, self._buffer, self.stats_, sink)

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if not self._started:
                self._exporter.start()
                self._started = True

    def _after_fork(self) -> None:
        """In a forked child the parent's exporter thread and queued events do not exist."""
        self.stats_.reinit_lock()
        self._start_lock = threading.Lock()
        self._buffer = EventBuffer(self.config.max_queue, self.stats_)
        self._exporter = self._new_exporter()
        self._started = False

    def _emit(
        self,
        run: Run,
        event_type: str,
        sequence: int,
        span_id: str | None,
        parent_span_id: str | None,
        status: str | None,
        duration_ms: float | None,
        attributes: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> None:
        if not self._enabled or self._closed:
            return
        try:
            self._ensure_started()
            priority = priority_of(event_type)
            event = build_event(
                event_type=event_type,
                run_id=run.run_id,
                trace_id=run.trace_id,
                agent_id=run.agent_id,
                agent_version=self.config.agent_version,
                sequence=sequence,
                span_id=span_id,
                parent_span_id=parent_span_id,
                status=status,
                duration_ms=duration_ms,
                attributes=clean_attributes(attributes, self.stats_),
                payload=payload,
                tags=run._tags if event_type == "run.started" else None,
                sdk_version=__version__,
            )
            redacted = self._redactor.apply(event, p0=priority == 0)
            if redacted is None:
                return
            if (
                redacted.get("payload") is not None
                and check_payload(redacted["payload"], self.stats_) is None
            ):
                del redacted["payload"]
            self.stats_.add("events_created")
            if self._buffer.put(redacted, priority):
                self._exporter.notify()
        except Exception:
            self.stats_.add("internal_errors")
