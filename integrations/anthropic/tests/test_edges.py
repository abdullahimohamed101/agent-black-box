"""Edge paths: cancellation, plain-function awaitables, unusual stream objects, helpers."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from abb_conformance import capture
from blackbox import BlackBox

from blackbox_anthropic import instrument
from blackbox_anthropic._util import count, number, preview
from tests.fakes import FakeAsyncStream, fake_client


def of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def test_helpers() -> None:
    assert count(3.0) == 3 and count(True) is None and count(-1) is None and count("3") is None
    assert number(1) == 1.0 and number(-0.1) is None and number("x") is None
    assert preview(object()).startswith("<") or preview(object())
    assert preview(SimpleNamespace(model_dump=lambda: {"a": 1})) == {"a": 1}

    class Bad:
        def model_dump(self) -> Any:
            raise RuntimeError

    assert preview(Bad()) == "<Bad>"


def test_keyboard_interrupt_is_recorded_as_cancelled_and_reraised() -> None:
    def drive(bb: BlackBox) -> None:
        with bb.run("r"):
            client = instrument(fake_client(error=KeyboardInterrupt()), bb)
            with pytest.raises(KeyboardInterrupt):
                client.messages.create(model="m")

    assert of(capture(drive), "llm.request.completed")[0]["status"] == "cancelled"


def test_plain_function_returning_an_awaitable() -> None:
    async def result() -> Any:
        return SimpleNamespace(usage=None)

    class Plain:
        def create(self, **kwargs: Any) -> Any:
            return result()

    class Failing:
        def create(self, **kwargs: Any) -> Any:
            async def boom() -> None:
                raise ValueError("x")

            return boom()

    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                ok = instrument(SimpleNamespace(messages=Plain()), bb)
                await ok.messages.create(model="m")
                bad = instrument(SimpleNamespace(messages=Failing()), bb)
                with pytest.raises(ValueError):
                    await bad.messages.create(model="m")

        asyncio.run(main())

    events = capture(drive)
    assert (
        len(of(events, "llm.request.completed")) == 1 and len(of(events, "llm.request.failed")) == 1
    )


def test_async_stream_abandoned_and_failing() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                client = instrument(
                    fake_client(chat=FakeAsyncStream([1, 2], fail_at=1), aio=True), bb
                )
                stream = await client.messages.create(model="m", stream=True)
                with pytest.raises(ConnectionError):
                    async for _ in stream:
                        pass
                client2 = instrument(fake_client(chat=FakeAsyncStream([1, 2]), aio=True), bb)
                stream2 = await client2.messages.create(model="m", stream=True)
                async for _ in stream2:
                    break
                await stream2.close()
                assert hasattr(stream2, "closed")

        asyncio.run(main())

    events = capture(drive)
    assert (
        of(events, "llm.request.failed")
        and of(events, "llm.request.completed")[0]["status"] == "cancelled"
    )


def test_stream_object_without_context_manager_methods() -> None:
    class Bare:
        def __iter__(self) -> Any:
            return iter([SimpleNamespace(usage=None)])

    def drive(bb: BlackBox) -> None:
        with bb.run("r"):
            client = instrument(fake_client(chat=Bare()), bb)
            with client.messages.create(model="m", stream=True) as stream:
                assert len(list(stream)) == 1

    assert of(capture(drive), "llm.request.completed")


def test_disabled_client_is_a_pass_through() -> None:
    bb = BlackBox(mode="disabled")
    with bb.run("r"):
        assert instrument(fake_client(), bb).messages.create(model="m") is not None
    assert bb.buffered_events() == []


def test_close_ends_the_span_immediately() -> None:
    from tests.fakes import FakeStream

    def drive(bb: BlackBox) -> None:
        with bb.run("r"):
            client = instrument(fake_client(chat=FakeStream([SimpleNamespace(usage=None)] * 3)), bb)
            wrapped = client.messages.create(model="m", stream=True)
            next(iter(wrapped))
            wrapped.close()
            assert bb.stats()["queue_size"] >= 3  # started + completed are already queued
            assert wrapped._call._ended is True

    events = capture(drive)
    assert of(events, "llm.request.completed")[0]["status"] == "cancelled"


@pytest.mark.parametrize("target", ["observe", "complete_usage", "finish", "record_usage"])
def test_bookkeeping_exceptions_never_reach_the_host(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    from blackbox import LlmCall

    import blackbox_anthropic.wrap as wrap
    from tests.fakes import FakeStream, chat_usage

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bookkeeping bug")

    if target == "observe":
        monkeypatch.setattr(wrap, "get", explode)
    elif target == "complete_usage":
        monkeypatch.setattr(wrap, "usage_of", explode)
    elif target == "finish":
        monkeypatch.setattr(wrap, "SyncStream", explode)
    else:
        monkeypatch.setattr(LlmCall, "record_usage", explode)
    sentinel = SimpleNamespace(usage=chat_usage())

    def drive(bb: BlackBox) -> None:
        with bb.run("r"):
            client = instrument(fake_client(chat=sentinel), bb)
            assert client.messages.create(model="m") is sentinel
            streamed = instrument(fake_client(chat=FakeStream([sentinel, sentinel])), bb)
            out = streamed.messages.create(model="m", stream=True)
            assert len(list(out)) == 2

    capture(drive)
