"""Wrap an OpenAI client so model calls become `llm.request.*` events (ADR-052).

    from blackbox_openai import instrument
    client = instrument(OpenAI(), bb)

`chat.completions.create` and `responses.create` are wrapped, sync and async, streaming included.
Outside a `bb.run(...)` a wrapped call is a transparent pass-through. The host's own exceptions
always propagate unchanged; the adapter's bookkeeping never raises.
"""

from __future__ import annotations

import functools
import inspect
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any

from blackbox import BlackBox, LlmCall, current_run

from blackbox_openai._util import count, get, number, preview, text

log = logging.getLogger("blackbox.openai")

CostFn = Callable[[str, str, int | None, int | None, int | None], float | None]
_TARGETS = (("chat", "completions", "create"), ("responses", "create"))
_MARK = "_abb_wrapped"


class Settings:
    def __init__(
        self, bb: BlackBox, provider: str, cost_fn: CostFn | None, capture_payloads: bool
    ) -> None:
        self.bb = bb
        self.provider = provider
        self.cost_fn = cost_fn
        self.capture = capture_payloads
        self.errors = 0

    def contained(self) -> None:
        self.errors += 1
        log.debug("blackbox-openai: bookkeeping failed and was contained", exc_info=True)


def usage_of(usage: Any) -> tuple[int | None, int | None, int | None]:
    """(input, output, cached) tokens from chat (`prompt_*`) or responses (`input_*`) usage."""
    tokens_in = count(get(usage, "prompt_tokens"))
    if tokens_in is None:
        tokens_in = count(get(usage, "input_tokens"))
    tokens_out = count(get(usage, "completion_tokens"))
    if tokens_out is None:
        tokens_out = count(get(usage, "output_tokens"))
    cached = count(get(get(usage, "prompt_tokens_details"), "cached_tokens"))
    if cached is None:
        cached = count(get(get(usage, "input_tokens_details"), "cached_tokens"))
    return tokens_in, tokens_out, cached


class Call:
    """One in-flight model call: ends its span exactly once."""

    def __init__(self, settings: Settings, span: LlmCall, request: Any, model: str) -> None:
        self.s = settings
        self.span = span
        self.model = model
        self.request = request  # preview text, only when payload capture is on
        self.usage: Any = None
        self._ended = False
        self._lock = threading.Lock()

    def observe(self, chunk: Any) -> None:
        """Keep the latest usage of a stream (chat: last chunk; responses: `response.completed`)."""
        try:
            usage = get(chunk, "usage") or get(get(chunk, "response"), "usage")
            if usage:
                self.usage = usage
        except Exception:
            self.s.contained()

    def _claim(self) -> bool:
        with self._lock:
            if self._ended:
                return False
            self._ended = True
            return True

    def complete(self, response: Any = None) -> None:
        if not self._claim():
            return
        try:
            usage = get(response, "usage") if response is not None else self.usage
            tokens_in, tokens_out, cached = usage_of(usage if usage else self.usage)
            cost = None
            if self.s.cost_fn is not None:
                try:
                    cost = self.s.cost_fn(
                        self.s.provider, self.model, tokens_in, tokens_out, cached
                    )
                except Exception:
                    self.s.contained()
            self.span.record_usage(
                tokens_in, tokens_out, cached_input_tokens=cached, cost_usd=number(cost)
            )
            if self.s.capture:
                self.span.set_payload({"request": self.request, "response": preview(response)})
        except Exception:
            self.s.contained()
        finally:
            self._end("success", None)

    def fail(self, exc: BaseException, status: str | None = None) -> None:
        if self._claim():
            self._end(status or ("error" if isinstance(exc, Exception) else "cancelled"), exc)

    def abandon(self) -> None:
        """The consumer stopped reading a stream (close or garbage collection)."""
        if self._claim():
            self._end("cancelled", None)

    def _end(self, status: str, exc: BaseException | None) -> None:
        try:
            self.span.end(status, exc)
        except Exception:
            self.s.contained()


def begin(settings: Settings, kwargs: dict[str, Any]) -> Call | None:
    """Start the span for a call; None means pass through (no run, disabled, or a failure)."""
    try:
        run = current_run()
        if run is None or not settings.bb.enabled:
            return None
        model = text(kwargs.get("model")) or "unknown"
        temperature = number(kwargs.get("temperature"))
        max_tokens = count(
            kwargs.get("max_completion_tokens")
            or kwargs.get("max_tokens")
            or kwargs.get("max_output_tokens")
        )
        span = run.llm_call(
            settings.provider,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            attributes={"framework.name": "openai"},
        )
        span.start()
        request = preview(kwargs.get("messages") or kwargs.get("input")) if settings.capture else ""
        return Call(settings, span, request, model)
    except Exception:
        settings.contained()
        return None


class SyncStream:
    """Transparent proxy over a streaming response that ends the span when the stream does."""

    def __init__(self, inner: Any, call: Call) -> None:
        self._inner = inner
        self._call = call
        self._it: Any = None
        weakref.finalize(self, call.abandon)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __iter__(self) -> SyncStream:
        if self._it is None:
            self._it = iter(self._inner)
        return self

    def __next__(self) -> Any:
        if self._it is None:
            self._it = iter(self._inner)
        try:
            chunk = next(self._it)
        except StopIteration:
            self._call.complete()
            raise
        except BaseException as exc:
            self._call.fail(exc)
            raise
        self._call.observe(chunk)
        return chunk

    def __enter__(self) -> SyncStream:
        enter = getattr(self._inner, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc_info: Any) -> Any:
        try:
            exit_ = getattr(self._inner, "__exit__", None)
            return exit_(*exc_info) if exit_ is not None else None
        finally:
            self._call.abandon()

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._call.abandon()


class AsyncStream:
    def __init__(self, inner: Any, call: Call) -> None:
        self._inner = inner
        self._call = call
        self._it: Any = None
        weakref.finalize(self, call.abandon)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __aiter__(self) -> AsyncStream:
        if self._it is None:
            self._it = self._inner.__aiter__()
        return self

    async def __anext__(self) -> Any:
        if self._it is None:
            self._it = self._inner.__aiter__()
        try:
            chunk = await self._it.__anext__()
        except StopAsyncIteration:
            self._call.complete()
            raise
        except BaseException as exc:
            self._call.fail(exc)
            raise
        self._call.observe(chunk)
        return chunk

    async def __aenter__(self) -> AsyncStream:
        enter = getattr(self._inner, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        try:
            exit_ = getattr(self._inner, "__aexit__", None)
            return await exit_(*exc_info) if exit_ is not None else None
        finally:
            self._call.abandon()

    async def close(self) -> None:
        try:
            await self._inner.close()
        finally:
            self._call.abandon()


def _finish(settings: Settings, call: Call, result: Any, streaming: bool) -> Any:
    """Turn a call's result into what the host receives, ending the span when appropriate."""
    try:
        if streaming and hasattr(result, "__aiter__"):
            return AsyncStream(result, call)
        if streaming and hasattr(result, "__iter__"):
            return SyncStream(result, call)
        call.complete(result)
    except Exception:
        settings.contained()
    return result


def _make_wrapper(orig: Callable[..., Any], settings: Settings) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(orig):

        @functools.wraps(orig)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            call = begin(settings, kwargs)
            if call is None:
                return await orig(*args, **kwargs)
            try:
                result = await orig(*args, **kwargs)
            except BaseException as exc:
                call.fail(exc)
                raise
            return _finish(settings, call, result, bool(kwargs.get("stream")))

        setattr(awrapper, _MARK, True)
        return awrapper

    @functools.wraps(orig)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = begin(settings, kwargs)
        if call is None:
            return orig(*args, **kwargs)
        try:
            result = orig(*args, **kwargs)
        except BaseException as exc:
            call.fail(exc)
            raise
        if inspect.isawaitable(result):  # an async client whose method is a plain function

            async def await_it() -> Any:
                try:
                    done = await result
                except BaseException as exc:
                    call.fail(exc)
                    raise
                return _finish(settings, call, done, bool(kwargs.get("stream")))

            return await_it()
        return _finish(settings, call, result, bool(kwargs.get("stream")))

    setattr(wrapper, _MARK, True)
    return wrapper


def instrument(
    client: Any,
    bb: BlackBox | None = None,
    *,
    provider: str = "openai",
    cost_fn: CostFn | None = None,
    capture_payloads: bool = False,
) -> Any:
    """Instrument `client` in place and return it. Idempotent; never raises."""
    try:
        settings = Settings(
            bb if isinstance(bb, BlackBox) else BlackBox(),
            provider if isinstance(provider, str) and provider else "openai",
            cost_fn if callable(cost_fn) else None,
            capture_payloads is True,
        )
        for path in _TARGETS:
            target = client
            for part in path[:-1]:
                target = getattr(target, part, None)
            orig = getattr(target, path[-1], None) if target is not None else None
            if not callable(orig) or getattr(orig, _MARK, False):
                continue
            setattr(target, path[-1], _make_wrapper(orig, settings))
    except Exception:
        log.debug("blackbox-openai: could not instrument the client", exc_info=True)
    return client
