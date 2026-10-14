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
import re
import threading
import time
import weakref
from collections.abc import Callable
from types import TracebackType
from typing import Any, TypeVar, cast, overload

from blackbox import ids
from blackbox.artifacts import KINDS, ArtifactUploader, _Upload
from blackbox.buffer import EventBuffer
from blackbox.config import Config, agent_slug
from blackbox.context import _current_run, _current_span
from blackbox.events import (
    EVENT_TYPE_RE,
    as_mapping,
    as_strings,
    build_event,
    check_payload,
    clean_attributes,
    clean_tags,
    name_attr,
    priority_of,
    span_kind,
    without_nul,
)
from blackbox.exporter import Exporter, HttpSink, LocalSink, Sink
from blackbox.policy import DISABLED, Decision, PolicyGuard, _run_guard
from blackbox.policy_spool import WarningSpool
from blackbox.propagation import TraceContext
from blackbox.redaction import PayloadMode, Redactor
from blackbox.stats import Stats
from blackbox.version import __version__

log = logging.getLogger("blackbox")
F = TypeVar("F", bound=Callable[..., Any])
_S = TypeVar("_S", bound="Span")

_SUCCESS, _ERROR, _CANCELLED = "success", "error", "cancelled"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")  # C0, DEL and C1 controls
# kind -> (started, completed, failed) event types; other kinds use the generic span events.
_TYPED_SPANS = {
    "tool": ("tool.call.started", "tool.call.completed", "tool.call.failed"),
    "agent": ("agent.started", "agent.completed", "agent.completed"),
    "shell": ("shell.command.started", "shell.command.completed", "shell.command.failed"),
}
_GENERIC_SPAN = ("span.started", "span.completed", "span.failed")
_RUN_END = {  # requested status -> (event type, event status)
    "success": ("run.completed", "success"),
    "error": ("run.failed", "error"),
    "timeout": ("run.completed", "timeout"),
    "blocked": ("run.completed", "blocked"),
    "cancelled": ("run.cancelled", "cancelled"),
}


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


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
        *,
        agent_id: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        self.run = run
        self.name = name_attr(name)
        if isinstance(parent, Span) and parent.run is not run:
            log.debug("ignoring a parent span that belongs to another run")
            parent = None
        parent = parent if isinstance(parent, Span) else None
        self.kind = span_kind(kind)
        self.span_id = ids.new_id(ids.SPAN)
        self._parent = parent
        # Identity (multi-agent traces): explicit, else the parent span's, else the run's. A span
        # whose parent is only known at start() (the current span) re-resolves there.
        self._own_agent_id = agent_slug(agent_id, "") or None if isinstance(agent_id, str) else None
        self._own_agent_version = name_attr(agent_version, 128) if _is_text(agent_version) else None
        self.agent_id, self.agent_version = self._resolve_identity()
        self._types = _TYPED_SPANS.get(self.kind, _GENERIC_SPAN)  # kind is always a str here
        self._base = self._base_attributes()
        self._attrs: dict[str, Any] = as_mapping(attributes)
        self._payload: dict[str, Any] | None = None
        self._t0 = 0.0
        self._started = False
        self._ended = False
        self._lock = threading.Lock()
        self._token: contextvars.Token[Span | None] | None = None

    def _resolve_identity(self) -> tuple[str, str | None]:
        parent = self._parent
        agent_id = self._own_agent_id or (parent.agent_id if parent else self.run.agent_id)
        version = self._own_agent_version or (
            parent.agent_version if parent else self.run.agent_version
        )
        return agent_id, version

    def _base_attributes(self) -> dict[str, Any]:
        if self.kind == "tool":
            return {"tool.name": self.name}
        if self.kind == "shell":
            return {"shell.command": self.name}
        return {"span.name": self.name, "span.kind": self.kind}

    # -- recording ---------------------------------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> None:
        if isinstance(key, str):
            self._attrs[key] = value

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self._attrs.update({k: v for k, v in as_mapping(attributes).items() if isinstance(k, str)})

    def set_payload(self, payload: dict[str, Any]) -> None:
        """Attach a payload to the closing event. Only sent in `PayloadMode.FULL`."""
        self._payload = dict(payload) if isinstance(payload, dict) else None

    def event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None = None,
        *,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        """A point event inside this span."""
        self.run._event(
            event_type,
            attributes,
            payload,
            status,
            span_id=self.span_id,
            agent_id=self.agent_id,
            agent_version=self.agent_version,
        )

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
        self.agent_id, self.agent_version = self._resolve_identity()
        self._before_open()
        self.run._emit(
            self._types[0],
            span_id=self.span_id,
            parent_span_id=self._parent_span_id(),
            attributes={**self._attrs, **self._base},
            agent_id=self.agent_id,
            agent_version=self.agent_version,
        )
        self._attrs = {}  # attributes given at start are on the started event only
        return self

    def _parent_span_id(self) -> str | None:
        """The parent span, else the span a continued run was handed (None in an ordinary run)."""
        return self._parent.span_id if self._parent else self.run._root_parent

    def _before_open(self) -> None:
        """Hook for spans that record something before their opening event (AgentSpan)."""

    def end(self, status: str = _SUCCESS, exc: BaseException | None = None) -> None:
        status = status if isinstance(status, str) else _SUCCESS
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
            parent_span_id=self._parent_span_id(),
            status=status,
            duration_ms=duration,
            attributes=attributes,
            payload=self._payload,
            agent_id=self.agent_id,
            agent_version=self.agent_version,
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


_LABEL_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")


def _label(value: object) -> str | None:
    """A short lowercase word (a tier or a region); anything else is dropped, not guessed."""
    if not isinstance(value, str):
        return None
    word = value.strip().lower()
    return word if _LABEL_RE.fullmatch(word) else None


class LlmCall(Span):
    """A model call. `record_usage` fills token counts and optional costs."""

    def __init__(
        self,
        run: "Run",
        provider: str,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent: Span | None = None,
    ) -> None:
        self.provider, self.model = name_attr(provider), name_attr(model)
        given = as_mapping(attributes)
        if temperature is not None:
            given["llm.temperature"] = temperature
        if max_tokens is not None:
            given["llm.max_tokens"] = max_tokens
        super().__init__(run, f"{self.provider}/{self.model}", "llm", given, parent)
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
        provider_cost_usd: float | None = None,
        cache_write_tokens: int | None = None,
        cache_write_1h_tokens: int | None = None,
        service_tier: str | None = None,
        data_residency: str | None = None,
    ) -> None:
        """`cost_usd` is your estimate; `provider_cost_usd` the provider's own (ADR-040).

        Billing modifiers (ADR-044), all optional: `cache_write_tokens` (part of `input_tokens`;
        `cache_write_1h_tokens` is the part of those with the 1-hour lifetime and counts as written
        even if you omit the total), `service_tier`
        (`standard`, `batch`, `flex`, `fast`, `ultrafast`) and `data_residency` (`global`, `us`,
        `eu` ...). Omit what you do not know: the server then prices the call as standard."""
        for key, value in (
            ("llm.input_tokens", input_tokens),
            ("llm.output_tokens", output_tokens),
            ("llm.cached_input_tokens", cached_input_tokens),
            ("llm.cache_write_tokens", cache_write_tokens),
            ("llm.cache_write_1h_tokens", cache_write_1h_tokens),
        ):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self._attrs[key] = value
        for key, word in (
            ("llm.service_tier", service_tier),
            ("llm.data_residency", data_residency),
        ):
            label = _label(word)
            if label is not None:
                self._attrs[key] = label
        if isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool) and cost_usd >= 0:
            self._attrs["cost.estimated_usd"] = float(cost_usd)
        if (
            isinstance(provider_cost_usd, (int, float))
            and not isinstance(provider_cost_usd, bool)
            and provider_cost_usd >= 0
        ):
            self._attrs["cost.provider_usd"] = float(provider_cost_usd)

    def end(self, status: str = _SUCCESS, exc: BaseException | None = None) -> None:
        if self._started and not self._ended:
            self._attrs.setdefault("llm.latency_ms", round((time.monotonic() - self._t0) * 1000, 3))
        super().end(status, exc)


class AgentSpan(Span):
    """A child agent inside the run: its own `agent_id`, recorded as `agent.started`/`completed`.

    Entering it first records `agent.spawned` as the delegating side (the parent's identity, in
    the delegating span when there is one), then opens the span with the child's identity, so
    every event inside carries the child's `agent_id` (spec §84). `input` and `set_result` are
    payloads: they follow `PayloadMode` and the redactor like every other payload.
    """

    def __init__(
        self,
        run: "Run",
        name: str,
        *,
        agent_id: str | None = None,
        agent_version: str | None = None,
        input: Any = None,
        attributes: dict[str, Any] | None = None,
        parent: Span | None = None,
    ) -> None:
        # The slug may need the delegator's identity (known at start), so keep the candidate only.
        candidate = agent_slug(agent_id, "") if isinstance(agent_id, str) else ""
        super().__init__(
            run,
            name,
            "agent",
            attributes,
            parent,
            agent_id=candidate
            or agent_slug(name if isinstance(name, str) else name_attr(name), ""),
            agent_version=agent_version,
        )
        self._input = input

    def set_result(self, result: Any) -> None:
        """The agent's result, sent as the payload of `agent.completed` (FULL mode only)."""
        self._payload = {"result": result}

    def _before_open(self) -> None:
        delegator = self._parent.agent_id if self._parent else self.run.agent_id
        delegator_version = self._parent.agent_version if self._parent else self.run.agent_version
        if not self._own_agent_id or self._own_agent_id == delegator:
            # No usable name, or the root's own: never silently share the delegator's identity.
            self._own_agent_id = self.run._child_slug(delegator)
            self.agent_id = self._own_agent_id
        self.run._emit(
            "agent.spawned",
            span_id=self._parent_span_id(),
            attributes={"agent.child_id": self.agent_id},
            payload=None if self._input is None else {"input": self._input},
            agent_id=delegator,
            agent_version=delegator_version,
        )


class Run:
    """One execution of an agent. Created by `BlackBox.run`; use as a context manager."""

    def __init__(
        self,
        bb: "BlackBox",
        name: str | None,
        metadata: dict[str, Any] | None,
        agent_id: str | None,
        tags: tuple[str, ...],
        *,
        remote: TraceContext | None = None,
        agent_version: str | None = None,
    ) -> None:
        self._bb = bb
        self.name = name if isinstance(name, str) else None
        self.remote = remote is not None  # a continued run (D13): it never starts or ends the run
        self._pid = os.getpid()  # a forked child that keeps using this Run is a remote emitter too
        self._inert = False
        self._root_parent: str | None = None
        self._agent_span: Span | None = None
        explicit = agent_slug(agent_id, "") if isinstance(agent_id, str) and agent_id else ""
        if remote is not None:
            self.run_id, self.trace_id = remote.run_id, remote.trace_id
            self._root_parent = remote.parent_span_id
            self._agent_known = bool(explicit or remote.agent_id)
            explicit = explicit or remote.agent_id or ""
        else:
            self.run_id = ids.new_id(ids.RUN)
            self.trace_id = ids.new_id(ids.TRACE)
            self._agent_known = False
        self.agent_id = explicit or bb.config.agent_id
        self.agent_version = (
            name_attr(agent_version, 128) if _is_text(agent_version) else bb.config.agent_version
        )
        self._tags = clean_tags(tuple(bb.config.tags) + as_strings(tags))
        self._children = itertools.count(1)
        self._seq = itertools.count(1)  # next() on a C counter is atomic: no lock on the hot path
        self._t0 = time.monotonic()
        self._ended = False
        self._lock = threading.Lock()
        self._token: contextvars.Token[Run | None] | None = None
        self._span_token: contextvars.Token[Span | None] | None = None
        attributes: dict[str, Any] = {}
        if self.name:
            attributes["run.name"] = name_attr(self.name)
        for key, value in as_mapping(metadata).items():
            attributes[f"metadata.{name_attr(key, 100).lower()}"] = value
        if remote is None:
            self._emit("run.started", attributes=attributes)

    @property
    def ended(self) -> bool:
        """True once the run's end event was recorded (adapters drop span events after it).

        Always False for a continued run: the root run owns the end, and late events are legal.
        """
        return self._ended

    # -- instrumentation ---------------------------------------------------------------------------

    def span(
        self,
        name: str,
        kind: str = "custom",
        attributes: dict[str, Any] | None = None,
        *,
        parent: Span | None = None,
    ) -> Span:
        return Span(self, name, kind, attributes, parent)  # arguments are coerced, never raised on

    def agent(
        self,
        name: str,
        *,
        agent_id: str | None = None,
        agent_version: str | None = None,
        input: Any = None,
        attributes: dict[str, Any] | None = None,
        parent: Span | None = None,
    ) -> AgentSpan:
        """A child agent in this run (D1): `with run.agent("researcher") as researcher: ...`."""
        return AgentSpan(
            self,
            name,
            agent_id=agent_id,
            agent_version=agent_version,
            input=input,
            attributes=attributes,
            parent=parent,
        )

    def _child_slug(self, parent_agent: str) -> str:
        """`<parent>-child-<n>`, within the 64-character slug limit."""
        suffix = f"-child-{next(self._children)}"
        return agent_slug(parent_agent[: 64 - len(suffix)] + suffix, "agent")

    def spawn_context(
        self,
        agent_id: str | None = None,
        *,
        parent: Span | None = None,
        input: Any = None,
    ) -> str:
        """Delegate to an agent in another process: record `agent.spawned`, return its context.

        Hand the string to the child (environment variable `BLACKBOX_TRACE_CONTEXT`, see
        `trace_context_from_env`); the child calls `bb.continue_run(...)`. Never raises.
        """
        if self._inert:  # a context for a run that does not exist would create a phantom run
            return ""
        try:
            span = self._delegating_span(parent)
            delegator = span.agent_id if span else self.agent_id
            version = span.agent_version if span else self.agent_version
            child = agent_slug(agent_id, "") if isinstance(agent_id, str) else ""
            if not child or child == delegator:
                child = self._child_slug(delegator)
            span_id = span.span_id if span else self._root_parent
            self._emit(
                "agent.spawned",
                span_id=span_id,
                attributes={"agent.child_id": child},
                payload=None if input is None else {"input": input},
                agent_id=delegator,
                agent_version=version,
            )
            return TraceContext(self.run_id, self.trace_id, span_id, child).serialize()
        except Exception:
            self._bb.stats_.add("internal_errors")
            return self.trace_context()

    def trace_context(self) -> str:
        """The context string for the current position, with no event (not a delegation)."""
        if self._inert:
            return ""
        span = self._delegating_span(None)
        span_id = span.span_id if span else self._root_parent
        return TraceContext(self.run_id, self.trace_id, span_id, None).serialize()

    def _delegating_span(self, parent: Span | None) -> "Span | None":
        if isinstance(parent, Span) and parent.run is self:
            return parent
        current = _current_span.get()
        return current if current is not None and current.run is self else None

    def llm_call(
        self,
        provider: str,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent: Span | None = None,
    ) -> LlmCall:
        return LlmCall(
            self,
            provider,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            attributes=attributes,
            parent=parent,
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
        if span is None or span.run is not self:
            self._event(event_type, attributes, payload, status, span_id=None)
            return
        self._event(
            event_type,
            attributes,
            payload,
            status,
            span_id=span.span_id,
            agent_id=span.agent_id,
            agent_version=span.agent_version,
        )

    def _event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None,
        payload: dict[str, Any] | None,
        status: str | None,
        *,
        span_id: str | None,
        agent_id: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        if not isinstance(event_type, str):
            event_type = ""
        status = status if isinstance(status, str) else None
        payload = payload if isinstance(payload, dict) else None
        if "." not in event_type and event_type:
            event_type = f"custom.{event_type}"
        if not EVENT_TYPE_RE.match(event_type) or len(event_type) > 64:
            self._bb.stats_.add("dropped_invalid")
            return
        self._emit(
            event_type,
            span_id=span_id,
            attributes=attributes,
            payload=payload,
            status=status,
            agent_id=agent_id,
            agent_version=agent_version,
        )

    def end(self, status: str = _SUCCESS, exc: BaseException | None = None) -> None:
        """Finish the run. Idempotent; `status` is success, error, timeout, blocked or cancelled.

        A continued run (`bb.continue_run`) never ends the run: this is a counted no-op.
        """
        if self.remote:
            self._bb.stats_.add("remote_run_end_ignored")
            return
        with self._lock:
            if self._ended:
                return
            self._ended = True
        event_type, event_status = _RUN_END.get(
            status if isinstance(status, str) else _SUCCESS, _RUN_END[_SUCCESS]
        )
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
        agent_id: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        if self._inert:
            return
        # `sequence` is only comparable within one emitter (§65.1): a continued run, or a forked
        # child that inherited this Run, sends none and the run is ordered by time (ADR-070).
        sequence = None if self.remote or os.getpid() != self._pid else next(self._seq)
        self._bb._emit(
            self,
            event_type,
            sequence,
            span_id,
            parent_span_id,
            status,
            duration_ms,
            attributes,
            payload,
            agent_id,
            agent_version,
        )

    def __enter__(self) -> "Run":
        self._token = _current_run.set(self)
        self._span_token = _current_span.set(None)
        if self.remote and self._agent_known:
            # The continued run is the child agent: its own agent span, parented to the context's.
            agent_span = Span(self, self.agent_id, "agent", agent_id=self.agent_id)
            self._agent_span = agent_span.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        agent_span, self._agent_span = self._agent_span, None
        if agent_span is not None:
            agent_span.__exit__(exc_type, exc, tb)
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
        self._spool = WarningSpool(self.stats_)
        self._exporter = self._new_exporter()
        self._uploader = ArtifactUploader(self.config, self.stats_)
        self._start_lock = threading.Lock()
        self._started = False
        self._closed = False
        self._policy: PolicyGuard | None = None
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

    def continue_run(
        self,
        context: "TraceContext | str | None",
        *,
        agent_id: str | None = None,
        agent_version: str | None = None,
    ) -> Run:
        """Join a run that another process started (D13); see `trace_context_from_env`.

        The returned `Run` records no `run.started`/`run.completed` (the root owns the run), parents
        its spans to the context's span and, as a context manager, wraps the block in an agent span
        when an agent id is known. An invalid or missing context gives an inert run and a warning.
        """
        parsed = context if isinstance(context, TraceContext) else TraceContext.parse(context)
        inert = parsed is None
        if parsed is None:
            parsed = TraceContext(ids.new_id(ids.RUN), ids.new_id(ids.TRACE))
        joined = Run(self, None, None, agent_id, (), remote=parsed, agent_version=agent_version)
        joined._inert = inert
        return joined

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

    def guard(
        self,
        capability: str,
        resource: str | None = None,
        risk_class: str | None = None,
        arguments: dict[str, Any] | None = None,
        *,
        on_error: str | None = None,
        run: "Run | None" = None,
    ) -> Decision:
        """Ask the policy service whether an action may happen (cooperative enforcement, D1).

        Returns a `Decision` when the action may proceed; raises `PolicyDenied` (or its subclasses
        `ApprovalDenied`, `ApprovalExpired`) when it must not, and `PolicyUnavailable` when the
        service cannot be asked and the fail mode is closed. Raises nothing else (INV-4). With
        `policy_mode="off"` (the default) it returns at once with `via="disabled"` and makes no
        network call. See `blackbox.policy` for the contract.
        """
        if self.config.policy_mode != "enforce":
            return DISABLED
        return self._policy_engine().guard(
            run if run is not None else _current_run.get(),
            capability,
            resource,
            risk_class,
            arguments,
            on_error,
        )

    async def aguard(
        self,
        capability: str,
        resource: str | None = None,
        risk_class: str | None = None,
        arguments: dict[str, Any] | None = None,
        *,
        on_error: str | None = None,
        run: "Run | None" = None,
    ) -> Decision:
        """`guard` for asyncio: the blocking call runs on a worker thread (`asyncio.to_thread`)."""
        if self.config.policy_mode != "enforce":
            return DISABLED
        return await _run_guard(
            self._policy_engine(),
            run if run is not None else _current_run.get(),
            capability,
            resource,
            risk_class,
            arguments,
            on_error,
        )

    def _policy_engine(self) -> PolicyGuard:
        engine = self._policy
        if engine is None:
            with self._start_lock:
                if self._policy is None:
                    self._policy = PolicyGuard(
                        self.config, self.stats_, on_contact=self._requeue_warnings
                    )
                engine = self._policy
        return engine

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
            label = name_attr(name or getattr(func, "__qualname__", "function"))

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

    def redact_text(self, text: str) -> str:
        """Apply this client's secret redaction to free text (never raises; "" on failure)."""
        try:
            return self._redactor.redact_text(text) if isinstance(text, str) else ""
        except Exception:
            self.stats_.add("internal_errors")
            return ""

    def upload_artifact(
        self,
        content: str | bytes,
        *,
        kind: str = "other",
        name: str | None = None,
        run: "Run | None" = None,
    ) -> str | None:
        """Queue text (a diff, terminal output) as an artifact; return its id (`art_...`) or None.

        Non-blocking. The text is redacted here, before it can leave the process (INV-5), and the
        upload only happens in `PayloadMode.FULL` over HTTP: otherwise nothing is stored and None is
        returned. A queue that is full or a failing server drops the upload and counts it.
        """
        try:
            target = run if run is not None else _current_run.get()
            if (
                target is None
                or not self.enabled
                or self.config.payload_mode is not PayloadMode.FULL
                or not self._uploader.active
            ):
                return None
            text = (
                content.decode("utf-8", errors="replace")
                if isinstance(content, (bytes, bytearray))
                else content
            )
            if not isinstance(text, str):
                return None
            data = self._redactor.redact_text(text).replace("\x00", "").encode("utf-8")
            artifact_id = ids.new_id("art")
            queued = self._uploader.enqueue(
                _Upload(
                    artifact_id,
                    target.run_id,
                    kind if kind in KINDS else "other",
                    self._artifact_name(name),
                    data,
                )
            )
            return artifact_id if queued else None
        except Exception:
            self.stats_.add("internal_errors")
            return None

    def _artifact_name(self, name: object) -> str | None:
        """A short printable label: redacted first, then cut (never a secret's prefix)."""
        if not isinstance(name, str) or not name:
            return None
        # NUL becomes a space before redaction: the label keeps its shape, no secret is reassembled
        return (
            _CONTROL.sub(" ", self._redactor.redact_text(name.replace("\x00", " ")))[:120] or None
        )

    def flush(self, timeout: float | None = None) -> bool:
        """Wait (bounded) until queued events were handed to the sink. Never raises."""
        try:
            wait = self.config.shutdown_timeout if timeout is None else timeout
            started = time.monotonic()
            self._requeue_warnings()  # one more try for warnings an outage made the exporter drop
            uploaded = self._uploader.flush(wait)
            if not self._started:
                return len(self._buffer) == 0 and uploaded
            left = max(0.0, wait - (time.monotonic() - started))
            return self._exporter.flush(left) and uploaded
        except Exception:
            self.stats_.add("internal_errors")
            return False

    def shutdown(self, timeout: float | None = None) -> bool:
        """Flush and stop the exporter. Idempotent; bounded by `timeout`; never raises."""
        if self._closed:
            return True
        self._closed = True
        try:
            wait = self.config.shutdown_timeout if timeout is None else timeout
            started = time.monotonic()
            self._requeue_warnings()
            uploaded = self._uploader.shutdown(wait)  # first: events may reference these artifacts
            left = max(0.0, wait - (time.monotonic() - started))
            return self._exporter.shutdown(left) and uploaded
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

    def _requeue_warnings(self) -> None:
        """The API answered: put back the `policy.warning` events the exporter had to drop (they
        keep their ids, so a copy that did get through is a duplicate, not a second event)."""
        spool = getattr(self, "_spool", None)
        if spool is None or len(spool) == 0:
            return
        for event in spool.take_all():
            self._buffer.put(event, 0)
        self._exporter.notify()

    def _new_exporter(self) -> Exporter:
        sink: Sink | None = None
        try:
            if self.config.mode == "http":
                sink = HttpSink(self.config, self.stats_, self._spool.offer, self._requeue_warnings)
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
        self._spool = WarningSpool(self.stats_)
        self._exporter = self._new_exporter()
        self._uploader = ArtifactUploader(self.config, self.stats_)
        self._started = False
        self._policy = None  # its lock and cached table belong to the parent process

    def _emit(
        self,
        run: Run,
        event_type: str,
        sequence: int | None,
        span_id: str | None,
        parent_span_id: str | None,
        status: str | None,
        duration_ms: float | None,
        attributes: dict[str, Any] | None,
        payload: dict[str, Any] | None,
        agent_id: str | None = None,
        agent_version: str | None = None,
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
                agent_id=agent_id or run.agent_id,
                agent_version=agent_version or self.config.agent_version,
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
            event = without_nul(event)  # before redaction, so rules see the final strings
            redacted = self._redactor.apply(event, p0=priority == 0)
            if redacted is None:
                return
            if redacted.get("payload") is not None:
                checked = check_payload(redacted["payload"], self.stats_)
                if checked is None:
                    del redacted["payload"]
                else:
                    redacted["payload"] = checked
            self.stats_.add("events_created")
            if self._buffer.put(redacted, priority):
                self._exporter.notify()
        except Exception:
            self.stats_.add("internal_errors")
