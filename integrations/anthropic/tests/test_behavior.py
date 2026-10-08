"""Mapping, streaming, passthrough and containment of the Anthropic wrapper (ADR-052)."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from abb_conformance import LLM_CACHED, LLM_INPUT, LLM_OUTPUT, ScenarioError, capture
from blackbox import BlackBox

from blackbox_anthropic import instrument
from tests.drivers import MESSAGE_JSON, real_available, real_client
from tests.fakes import FakeAsyncStream, FakeStream, chat_usage, fake_client


def of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def in_run(fn: Any, **options: Any) -> list[dict[str, Any]]:
    def drive(bb: BlackBox) -> None:
        with bb.run("r"):
            fn(bb)

    return capture(drive, **options)


def stream_events() -> list[Any]:
    usage = chat_usage()
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=usage.input_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                    output_tokens=1,
                )
            ),
        ),
        SimpleNamespace(type="content_block_delta"),
        SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=LLM_OUTPUT)),
        SimpleNamespace(type="message_stop"),
    ]


def test_input_tokens_are_the_whole_prompt_with_cache_split_out() -> None:
    def go(bb: BlackBox) -> None:
        client = instrument(fake_client(), bb)
        client.messages.create(model="claude-x", max_tokens=64, temperature=0.3, messages=[])

    events = in_run(go)
    a = of(events, "llm.request.completed")[0]["attributes"]
    assert (a["llm.input_tokens"], a["llm.output_tokens"], a["llm.cached_input_tokens"]) == (
        LLM_INPUT,
        LLM_OUTPUT,
        LLM_CACHED,
    )
    started = of(events, "llm.request.started")[0]["attributes"]
    assert started["llm.provider"] == "anthropic" and started["llm.max_tokens"] == 64
    assert started["llm.temperature"] == 0.3 and started["framework.name"] == "anthropic"


def test_cache_creation_tokens_count_toward_input() -> None:
    usage = SimpleNamespace(
        input_tokens=10, output_tokens=2, cache_read_input_tokens=5, cache_creation_input_tokens=7
    )

    def go(bb: BlackBox) -> None:
        instrument(fake_client(chat=SimpleNamespace(usage=usage)), bb).messages.create(model="m")

    a = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert a["llm.input_tokens"] == 22 and a["llm.cached_input_tokens"] == 5


def test_cost_fn_and_its_failure() -> None:
    seen: list[Any] = []

    def go(bb: BlackBox) -> None:
        client = instrument(fake_client(), bb, cost_fn=lambda *a: seen.append(a) or 0.25)
        client.messages.create(model="m")

    events = in_run(go)
    assert of(events, "llm.request.completed")[0]["attributes"]["cost.estimated_usd"] == 0.25
    assert seen == [("anthropic", "m", LLM_INPUT, LLM_OUTPUT, LLM_CACHED)]

    def boom(*a: Any) -> float:
        raise RuntimeError("x")

    def go2(bb: BlackBox) -> None:
        assert instrument(fake_client(), bb, cost_fn=boom).messages.create(model="m")

    attrs = of(in_run(go2), "llm.request.completed")[0]["attributes"]
    assert "cost.estimated_usd" not in attrs and attrs["llm.input_tokens"] == LLM_INPUT


def test_pass_through_outside_a_run_and_when_disabled() -> None:
    sentinel = SimpleNamespace(usage=chat_usage())

    def drive(bb: BlackBox) -> None:
        assert instrument(fake_client(chat=sentinel), bb).messages.create(model="m") is sentinel

    assert capture(drive) == []
    off = BlackBox(mode="disabled")
    with off.run("r"):
        assert instrument(fake_client(chat=sentinel), off).messages.create(model="m") is sentinel


def test_host_errors_propagate_unchanged_and_are_recorded() -> None:
    error = ScenarioError("overloaded")

    def go(bb: BlackBox) -> None:
        with pytest.raises(ScenarioError) as info:
            instrument(fake_client(error=error), bb).messages.create(model="m")
        assert info.value is error

    failed = of(in_run(go), "llm.request.failed")[0]
    assert failed["status"] == "error" and failed["attributes"]["error.type"] == "ScenarioError"


def test_idempotent_and_garbage_tolerant() -> None:
    def go(bb: BlackBox) -> None:
        client = fake_client()
        instrument(instrument(client, bb), bb)
        client.messages.create(model="m")
        for junk in (None, 5, object(), SimpleNamespace(messages=None)):
            assert instrument(junk, bb) is junk

    assert len(of(in_run(go), "llm.request.started")) == 1


def test_bookkeeping_failure_never_breaks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from blackbox import Run

    def explode(self: Run, *a: Any, **k: Any) -> None:
        raise RuntimeError("sdk bug")

    monkeypatch.setattr(Run, "llm_call", explode)
    sentinel = SimpleNamespace(usage=chat_usage())

    def go(bb: BlackBox) -> None:
        assert instrument(fake_client(chat=sentinel), bb).messages.create(model="m") is sentinel

    in_run(go)


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": -1, "output_tokens": "3"}, 7])
def test_unusable_usage_is_ignored(usage: Any) -> None:
    def go(bb: BlackBox) -> None:
        instrument(fake_client(chat=SimpleNamespace(usage=usage)), bb).messages.create(model="m")

    attrs = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert "llm.input_tokens" not in attrs and "llm.output_tokens" not in attrs


def test_payloads_are_off_unless_requested_and_still_redacted() -> None:
    def go(bb: BlackBox, on: bool) -> None:
        client = instrument(fake_client(), bb, capture_payloads=on)
        client.messages.create(model="m", messages=[{"content": "my private prompt"}])

    off = in_run(lambda bb: go(bb, False), payload_mode="full")
    assert all("payload" not in e for e in off) and "private prompt" not in str(off)
    on = in_run(lambda bb: go(bb, True), payload_mode="full")
    assert "private prompt" in str(of(on, "llm.request.completed")[0]["payload"]["request"])
    assert all("payload" not in e for e in in_run(lambda bb: go(bb, True)))


def test_sync_stream_accumulates_usage_across_events() -> None:
    def go(bb: BlackBox) -> None:
        client = instrument(fake_client(chat=FakeStream(stream_events())), bb)
        with client.messages.create(model="m", max_tokens=8, messages=[], stream=True) as stream:
            assert len(list(stream)) == 4

    a = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert (a["llm.input_tokens"], a["llm.output_tokens"], a["llm.cached_input_tokens"]) == (
        LLM_INPUT,
        LLM_OUTPUT,
        LLM_CACHED,
    )


def test_stream_failure_and_abandonment() -> None:
    def failing(bb: BlackBox) -> None:
        client = instrument(fake_client(chat=FakeStream(stream_events(), fail_at=2)), bb)
        with pytest.raises(ConnectionError):
            list(client.messages.create(model="m", stream=True))

    assert of(in_run(failing), "llm.request.failed")

    def abandoned(bb: BlackBox) -> None:
        client = instrument(fake_client(chat=FakeStream(stream_events())), bb)
        wrapped = client.messages.create(model="m", stream=True)
        next(iter(wrapped))
        wrapped.close()

    assert of(in_run(abandoned), "llm.request.completed")[0]["status"] == "cancelled"


def test_async_client_calls_and_streams() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                client = instrument(fake_client(aio=True), bb)
                await asyncio.gather(*(client.messages.create(model="m") for _ in range(5)))
                streaming = instrument(
                    fake_client(chat=FakeAsyncStream(stream_events()), aio=True), bb
                )
                result = await streaming.messages.create(model="m", stream=True)
                async with result as stream:
                    assert len([c async for c in stream]) == 4

        asyncio.run(main())

    events = capture(drive)
    done = of(events, "llm.request.completed")
    assert len(done) == 6 and all(e["attributes"]["llm.input_tokens"] == LLM_INPUT for e in done)


real = pytest.mark.skipif(not real_available(), reason="anthropic is not installed")


@real
def test_real_client_error_status_is_recorded_and_raised() -> None:
    import anthropic
    import httpx2 as httpx

    client = real_client(
        lambda request: httpx.Response(
            500, json={"type": "error", "error": {"type": "api_error", "message": "down"}}
        )
    )

    def go(bb: BlackBox) -> None:
        with pytest.raises(anthropic.InternalServerError):
            instrument(client, bb).messages.create(model="m", max_tokens=1, messages=[])

    failed = of(in_run(go), "llm.request.failed")[0]
    assert failed["attributes"]["error.type"] == "InternalServerError"


@real
def test_real_streaming_events() -> None:
    import httpx2 as httpx

    def sse(request: httpx.Request) -> httpx.Response:
        def frame(name: str, data: dict[str, Any]) -> str:
            return f"event: {name}\ndata: {json.dumps(data)}\n\n"

        start = {
            **MESSAGE_JSON,
            "content": [],
            "usage": {**MESSAGE_JSON["usage"], "output_tokens": 1},
        }
        body = (
            frame("message_start", {"type": "message_start", "message": start})
            + frame(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 30},
                },
            )
            + frame("message_stop", {"type": "message_stop"})
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    def go(bb: BlackBox) -> None:
        client = instrument(real_client(sse), bb)
        stream = client.messages.create(model="m1", max_tokens=8, messages=[], stream=True)
        assert len(list(stream)) == 3

    a = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert (a["llm.input_tokens"], a["llm.output_tokens"], a["llm.cached_input_tokens"]) == (
        LLM_INPUT,
        LLM_OUTPUT,
        LLM_CACHED,
    )


@real
def test_real_async_client() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                client = instrument(real_client(aio=True), bb)
                await asyncio.gather(
                    *(
                        client.messages.create(model="m1", max_tokens=1, messages=[])
                        for _ in range(4)
                    )
                )
                await client.close()

        asyncio.run(main())

    assert len(of(capture(drive), "llm.request.completed")) == 4
