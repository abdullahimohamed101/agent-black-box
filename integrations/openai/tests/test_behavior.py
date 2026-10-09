"""Mapping, streaming, passthrough and containment of the OpenAI wrapper (ADR-052)."""

import asyncio
import gc
from types import SimpleNamespace
from typing import Any

import pytest
from abb_conformance import LLM_CACHED, LLM_INPUT, LLM_OUTPUT, ScenarioError, capture
from blackbox import BlackBox

from blackbox_openai import instrument
from tests.drivers import CHAT_JSON, real_available, real_client
from tests.fakes import FakeAsyncStream, FakeStream, chat_usage, fake_client, responses_usage


def of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


def in_run(fn: Any, **options: Any) -> list[dict[str, Any]]:
    def drive(bb: BlackBox) -> None:
        with bb.run("r"):
            fn(bb)

    return capture(drive, **options)


def test_chat_and_responses_usage_mapping() -> None:
    def go(bb: BlackBox) -> None:
        client = instrument(fake_client(), bb)
        client.chat.completions.create(model="gpt-x", temperature=0.5, max_completion_tokens=64)
        client.responses.create(model="gpt-x", input="hi")

    events = in_run(go)
    done = of(events, "llm.request.completed")
    assert len(done) == 2
    for e in done:
        a = e["attributes"]
        assert (a["llm.input_tokens"], a["llm.output_tokens"], a["llm.cached_input_tokens"]) == (
            LLM_INPUT,
            LLM_OUTPUT,
            LLM_CACHED,
        )
    started = of(events, "llm.request.started")[0]["attributes"]
    assert started["llm.temperature"] == 0.5 and started["llm.max_tokens"] == 64
    assert started["llm.model"] == "gpt-x" and started["framework.name"] == "openai"


def test_cost_fn_and_its_failure() -> None:
    seen: list[Any] = []

    def go(bb: BlackBox) -> None:
        instrument(
            fake_client(), bb, cost_fn=lambda *a: seen.append(a) or 0.5
        ).chat.completions.create(model="m")

    events = in_run(go)
    assert of(events, "llm.request.completed")[0]["attributes"]["cost.estimated_usd"] == 0.5
    assert seen == [("openai", "m", LLM_INPUT, LLM_OUTPUT, LLM_CACHED)]

    def boom(*a: Any) -> float:
        raise RuntimeError("x")

    def go2(bb: BlackBox) -> None:
        client = instrument(fake_client(), bb, cost_fn=boom)
        assert client.chat.completions.create(model="m") is not None

    events = in_run(go2)
    attrs = of(events, "llm.request.completed")[0]["attributes"]
    assert "cost.estimated_usd" not in attrs and attrs["llm.input_tokens"] == LLM_INPUT


def test_outside_a_run_the_call_is_a_pass_through() -> None:
    sentinel = SimpleNamespace(usage=chat_usage())
    holder: list[Any] = []

    def drive(bb: BlackBox) -> None:
        client = fake_client(chat=sentinel)
        holder.append(client)
        assert instrument(client, bb).chat.completions.create(model="m") is sentinel

    assert capture(drive) == []


def test_host_errors_propagate_unchanged_and_are_recorded() -> None:
    error = ScenarioError("rate limited")

    def go(bb: BlackBox) -> None:
        with pytest.raises(ScenarioError) as info:
            instrument(fake_client(error=error), bb).chat.completions.create(model="m")
        assert info.value is error

    events = in_run(go)
    failed = of(events, "llm.request.failed")[0]
    assert failed["status"] == "error" and failed["attributes"]["error.type"] == "ScenarioError"


def test_instrument_is_idempotent_and_survives_garbage() -> None:
    def go(bb: BlackBox) -> None:
        client = fake_client()
        instrument(instrument(client, bb), bb)
        client.chat.completions.create(model="m")
        for junk in (None, 5, object(), SimpleNamespace(chat=None)):
            assert instrument(junk, bb) is junk

    assert len(of(in_run(go), "llm.request.started")) == 1


def test_bookkeeping_failure_never_breaks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from blackbox import Run

    def explode(self: Run, *a: Any, **k: Any) -> None:
        raise RuntimeError("sdk bug")

    monkeypatch.setattr(Run, "llm_call", explode)
    sentinel = SimpleNamespace(usage=chat_usage())

    def go(bb: BlackBox) -> None:
        assert (
            instrument(fake_client(chat=sentinel), bb).chat.completions.create(model="m")
            is sentinel
        )

    in_run(go)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": -1, "completion_tokens": "3"}, 7])
def test_unusable_usage_is_ignored(usage: Any) -> None:
    def go(bb: BlackBox) -> None:
        instrument(fake_client(chat=SimpleNamespace(usage=usage)), bb).chat.completions.create(
            model="m"
        )

    attrs = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert "llm.input_tokens" not in attrs and "llm.output_tokens" not in attrs


def test_payloads_are_off_unless_requested_and_still_redacted() -> None:
    def go(bb: BlackBox, capture_payloads: bool) -> None:
        client = instrument(fake_client(), bb, capture_payloads=capture_payloads)
        client.chat.completions.create(model="m", messages=[{"content": "my private prompt"}])

    off = in_run(lambda bb: go(bb, False), payload_mode="full")
    assert all("payload" not in e for e in off) and "private prompt" not in str(off)
    on = in_run(lambda bb: go(bb, True), payload_mode="full")
    assert "private prompt" in str(of(on, "llm.request.completed")[0]["payload"]["request"])
    gated = in_run(lambda bb: go(bb, True))
    assert all("payload" not in e for e in gated)


# -- streaming -----------------------------------------------------


def chat_chunks() -> list[Any]:
    return [
        SimpleNamespace(choices=[], usage=None),
        SimpleNamespace(choices=[], usage=chat_usage()),
    ]


def test_sync_stream_ends_with_usage_when_exhausted() -> None:
    def go(bb: BlackBox) -> None:
        stream = FakeStream(chat_chunks())
        client = instrument(fake_client(chat=stream), bb)
        result = client.chat.completions.create(model="m", stream=True)
        assert len(list(result)) == 2

    events = in_run(go)
    attrs = of(events, "llm.request.completed")[0]["attributes"]
    assert attrs["llm.input_tokens"] == LLM_INPUT and len(of(events, "llm.request.started")) == 1


def test_responses_stream_reads_usage_from_the_completed_event() -> None:
    chunks = [
        SimpleNamespace(type="response.output_text.delta"),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(usage=responses_usage())
        ),
    ]

    def go(bb: BlackBox) -> None:
        client = instrument(fake_client(responses=FakeStream(chunks)), bb)
        with client.responses.create(model="m", input="x", stream=True) as stream:
            for _ in stream:
                pass

    attrs = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert attrs["llm.output_tokens"] == LLM_OUTPUT


def test_stream_failure_and_abandonment() -> None:
    def failing(bb: BlackBox) -> None:
        client = instrument(fake_client(chat=FakeStream(chat_chunks(), fail_at=1)), bb)
        with pytest.raises(ConnectionError):
            list(client.chat.completions.create(model="m", stream=True))

    assert (
        of(in_run(failing), "llm.request.failed")[0]["attributes"]["error.type"]
        == "ConnectionError"
    )

    def abandoned(bb: BlackBox) -> None:
        stream = FakeStream(chat_chunks())
        client = instrument(fake_client(chat=stream), bb)
        wrapped = client.chat.completions.create(model="m", stream=True)
        next(iter(wrapped))
        wrapped.close()
        wrapped.close()

    done = of(in_run(abandoned), "llm.request.completed")
    assert len(done) == 1 and done[0]["status"] == "cancelled"

    def garbage_collected(bb: BlackBox) -> None:
        client = instrument(fake_client(chat=FakeStream(chat_chunks())), bb)
        wrapped = client.chat.completions.create(model="m", stream=True)
        next(iter(wrapped))
        del wrapped
        gc.collect()

    assert of(in_run(garbage_collected), "llm.request.completed")[0]["status"] == "cancelled"


# -- async ---------------------------------------------------------


def test_async_client_calls_and_streams() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                client = instrument(fake_client(aio=True), bb)
                await asyncio.gather(*(client.chat.completions.create(model="m") for _ in range(5)))
                streaming = instrument(
                    fake_client(chat=FakeAsyncStream(chat_chunks()), aio=True), bb
                )
                result = await streaming.chat.completions.create(model="m", stream=True)
                async with result as stream:
                    assert len([c async for c in stream]) == 2

        asyncio.run(main())

    events = capture(drive)
    assert len(of(events, "llm.request.completed")) == 6
    assert all(
        e["attributes"]["llm.input_tokens"] == LLM_INPUT
        for e in of(events, "llm.request.completed")
    )


def test_async_error_propagates() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                client = instrument(fake_client(error=ScenarioError("x"), aio=True), bb)
                with pytest.raises(ScenarioError):
                    await client.chat.completions.create(model="m")

        asyncio.run(main())

    assert of(capture(drive), "llm.request.failed")


# -- the real openai package (offline, mock transport) -------------

real = pytest.mark.skipif(not real_available(), reason="openai is not installed")


@real
def test_real_client_error_status_is_recorded_and_raised() -> None:
    import httpx
    import openai

    client = real_client(lambda request: httpx.Response(500, json={"error": {"message": "down"}}))

    def go(bb: BlackBox) -> None:
        with pytest.raises(openai.InternalServerError):
            instrument(client, bb).chat.completions.create(model="m", messages=[])

    failed = of(in_run(go), "llm.request.failed")[0]
    assert failed["attributes"]["error.type"] == "InternalServerError"


@real
def test_real_streaming_with_usage_chunk() -> None:
    import httpx

    def sse(request: httpx.Request) -> httpx.Response:
        def chunk(extra: dict[str, Any]) -> str:
            base = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m1"}
            import json

            return "data: " + json.dumps({**base, **extra}) + "\n\n"

        body = (
            chunk({"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]})
            + chunk({"choices": [], "usage": CHAT_JSON["usage"]})
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    def go(bb: BlackBox) -> None:
        client = instrument(real_client(sse), bb)
        stream = client.chat.completions.create(
            model="m1", messages=[], stream=True, stream_options={"include_usage": True}
        )
        assert len(list(stream)) == 2

    attrs = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert attrs["llm.input_tokens"] == LLM_INPUT and attrs["llm.cached_input_tokens"] == LLM_CACHED


@real
def test_real_async_client() -> None:
    def drive(bb: BlackBox) -> None:
        async def main() -> None:
            async with bb.run("r"):
                client = instrument(real_client(aio=True), bb)
                await asyncio.gather(
                    *(client.chat.completions.create(model="m1", messages=[]) for _ in range(4))
                )
                await client.close()

        asyncio.run(main())

    events = capture(drive)
    assert len(of(events, "llm.request.completed")) == 4


@real
def test_real_responses_api() -> None:
    import httpx

    body = {
        "id": "r",
        "object": "response",
        "created_at": 1,
        "model": "m1",
        "status": "completed",
        "output": [],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }

    def go(bb: BlackBox) -> None:
        client = instrument(real_client(lambda r: httpx.Response(200, json=body)), bb)
        client.responses.create(model="m1", input="hi")

    attrs = of(in_run(go), "llm.request.completed")[0]["attributes"]
    assert attrs["llm.input_tokens"] == LLM_INPUT and attrs["llm.cached_input_tokens"] == LLM_CACHED


@real
def test_real_with_options_and_raw_response() -> None:
    def go(bb: BlackBox) -> None:
        client = instrument(real_client(), bb)
        client.with_options(timeout=3).chat.completions.create(model="m1", messages=[])
        raw = client.chat.completions.with_raw_response.create(model="m1", messages=[])
        assert raw.parse().choices  # the caller can still parse

    done = of(in_run(go), "llm.request.completed")
    assert len(done) == 2
    assert all(e["attributes"]["llm.input_tokens"] == LLM_INPUT for e in done)
