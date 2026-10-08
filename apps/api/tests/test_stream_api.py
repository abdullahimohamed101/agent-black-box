"""GET /v1/runs/{id}/stream over a real socket, against real PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from starlette.requests import ClientDisconnect

from abb_api.runs.event_queries import EventQueries
from abb_api.streaming.sse import SseResponse
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids
from tests.stream_fixtures import Live, messages, send, serve
from tests.test_runs_api import error


@pytest.fixture
async def live(api: Api, runtime_database_url: str) -> AsyncIterator[Live]:
    async for instance in serve(api, runtime_database_url):
        yield instance


async def take(
    stream: Any, count: int, *, kinds: tuple[str, ...] = ("trace_event",), seconds: float = 8
) -> list[dict[str, Any]]:
    """The next `count` messages whose event is in `kinds`, or fail on timeout."""
    found: list[dict[str, Any]] = []

    async def read() -> None:
        async for message in stream:
            if message.get("event") in kinds:
                found.append(message)
                if len(found) == count:
                    return

    await asyncio.wait_for(read(), seconds)
    return found


async def events_of(
    live: Live, run: dict[str, str], count: int, **params: Any
) -> list[dict[str, Any]]:
    async with live.open(run["run_id"], **params) as response:
        assert response.status_code == 200, await response.aread()
        return await take(messages(response), count)


# ------------------------------------------------------------------ before the stream starts


async def test_authentication_scope_and_visibility_are_enforced_before_any_stream_bytes(
    live: Live,
) -> None:
    run = make_run_ids()
    await send(live.api, run, 1)
    unknown = make_run_ids()
    cases: list[tuple[dict[str, Any], dict[str, str], int, str]] = [
        ({"token": "nope"}, run, 401, "API_KEY_INVALID"),
        ({"token": "ingest_only"}, run, 403, "INSUFFICIENT_SCOPE"),
        ({"token": "other"}, run, 404, "RUN_NOT_FOUND"),
        ({"token": "beta"}, run, 404, "RUN_NOT_FOUND"),  # another project of the same workspace
        ({"token": "reader"}, unknown, 404, "RUN_NOT_FOUND"),
    ]
    for kwargs, target, status, code in cases:
        async with live.open(target["run_id"], **kwargs) as response:
            await response.aread()
            body = response.json()
            assert response.status_code == status, (kwargs, body)
            assert body["error"]["code"] == code, (kwargs, body)
            assert response.headers["content-type"].startswith("application/json")


async def test_the_stream_response_headers_forbid_caching_and_proxy_buffering(live: Live) -> None:
    run = make_run_ids()
    await send(live.api, run, 1)
    async with live.open(run["run_id"]) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-request-id"]


# ------------------------------------------------------------------ delivery


async def test_events_ingested_after_connecting_arrive_without_any_client_polling(
    live: Live,
) -> None:
    run = make_run_ids()
    first = await send(live.api, run, 1)
    async with live.open(run["run_id"]) as response:
        stream = messages(response)
        (replayed,) = await take(stream, 1)
        assert replayed["id"] == first["event_id"]
        second = await send(live.api, run, 2)
        third = await send(live.api, run, 3)
        got = await take(stream, 2, seconds=3)  # live: wake-up or the 0.2 s fallback poll
    assert [m["id"] for m in got] == [second["event_id"], third["event_id"]]
    data = got[0]["data"]
    assert data["event_id"] == second["event_id"] and data["event_type"] == "tool.call.completed"
    assert data["payload"] is None


async def test_a_new_stream_replays_the_run_from_the_start_in_arrival_order(live: Live) -> None:
    run = make_run_ids()
    sent = [await send(live.api, run, n) for n in (3, 1, 2)]  # arrival order, not sequence order
    got = await events_of(live, run, 3)
    assert [m["id"] for m in got] == [e["event_id"] for e in sent]


async def test_payloads_are_never_streamed_only_their_presence(live: Live) -> None:
    run = make_run_ids()
    await send(live.api, run, 1, payload={"secret": "hunter2"})
    (message,) = await events_of(live, run, 1)
    assert message["data"]["payload"] is None and message["data"]["has_payload"] is True
    assert "hunter2" not in str(message)


async def test_a_stream_carries_only_its_own_run(live: Live) -> None:
    mine, theirs = make_run_ids(), make_run_ids()
    await send(live.api, mine, 1)
    await send(live.api, theirs, 1)  # same project, other run
    other_tenant = make_run_ids()
    response = await live.api.client.post(
        "/v1/events/batch",
        json={
            "events": [
                __import__("tests.ingest_helpers", fromlist=["x"]).wire_event(other_tenant, 1)
            ]
        },
        headers=live.api.headers("other"),
    )
    assert response.status_code == 202
    async with live.open(mine["run_id"]) as response:
        stream = messages(response)
        got = await take(stream, 1)
        await send(live.api, theirs, 2)
        extra = []
        try:
            extra = await take(stream, 1, seconds=1)
        except TimeoutError:
            pass
    assert [m["data"]["run_id"] for m in got] == [mine["run_id"]]
    assert extra == []


# ------------------------------------------------------------------ resume


async def test_resuming_from_last_event_id_sends_what_was_missed(
    api: Api, runtime_database_url: str
) -> None:
    async for live in serve(api, runtime_database_url, stream_overlap_seconds=0):
        run = make_run_ids()
        sent = [await send(api, run, n) for n in (1, 2, 3, 4)]
        resume_from = sent[1]["event_id"]
        by_header = await events_of(live, run, 3, headers={"Last-Event-ID": resume_from})
        by_query = await events_of(live, run, 3, last_event_id=resume_from)
        # `since` is inclusive, so the resume event itself comes back once; clients de-duplicate
        expected = [e["event_id"] for e in sent[1:]]
        assert [m["id"] for m in by_header] == expected
        assert [m["id"] for m in by_query] == expected
        garbage = await events_of(live, run, 4, last_event_id="evt_not-real")
        unknown = await events_of(live, run, 4, last_event_id=make_run_ids()["run_id"])
        assert len(garbage) == len(unknown) == 4  # an unusable id means "from the start"


async def test_a_resume_window_repeats_recent_events_which_clients_drop_by_id(live: Live) -> None:
    run = make_run_ids()
    sent = [await send(live.api, run, n) for n in (1, 2, 3)]
    got = await events_of(live, run, 3, last_event_id=sent[2]["event_id"])  # 30 s overlap
    ids = [m["id"] for m in got]
    assert ids == [e["event_id"] for e in sent] and len(set(ids)) == 3


# ------------------------------------------------------------------ lifecycle


async def test_a_finished_run_ends_its_stream_after_a_quiet_period(live: Live) -> None:
    run = make_run_ids()
    await send(live.api, run, 1, event_type="run.started")
    done = await send(live.api, run, 2, event_type="run.completed")
    async with live.open(run["run_id"]) as response:
        kinds = [m["event"] async for m in messages(response) if "event" in m]
    assert kinds == ["trace_event", "trace_event", "run_end"]  # and the connection closed by itself
    assert done["event_id"]


async def test_late_events_after_completion_are_still_delivered_and_delay_the_end(
    live: Live,
) -> None:
    run = make_run_ids()
    await send(live.api, run, 1, event_type="run.completed")
    async with live.open(run["run_id"]) as response:
        stream = messages(response)
        await take(stream, 1)
        late = await send(live.api, run, 2)  # inside the 0.5 s quiet period
        got = await take(stream, 1, seconds=3)
        assert got[0]["id"] == late["event_id"]
        end = await take(stream, 1, kinds=("run_end",), seconds=5)
        assert end[0]["data"]["reason"] == "run_finished"


async def test_a_run_that_starts_again_is_not_ended(live: Live) -> None:
    run = make_run_ids()
    await send(live.api, run, 1, event_type="run.completed")
    await send(live.api, run, 2, event_type="run.started")  # resumed: not finished any more
    async with live.open(run["run_id"]) as response:
        stream = messages(response)
        await take(stream, 2)
        with pytest.raises(TimeoutError):  # well past the 0.5 s quiet period: still open
            await take(stream, 1, kinds=("run_end",), seconds=1.5)


async def test_a_late_event_restarts_the_quiet_period(api: Api, runtime_database_url: str) -> None:
    async for live in serve(api, runtime_database_url, stream_end_quiet_seconds=1.0):
        run = make_run_ids()
        await send(api, run, 1, event_type="run.completed")
        async with live.open(run["run_id"]) as response:
            stream = messages(response)
            await take(stream, 1)
            await asyncio.sleep(0.7)
            await send(api, run, 2)
            await take(stream, 1, seconds=3)
            delivered = asyncio.get_running_loop().time()
            await take(stream, 1, kinds=("run_end",), seconds=5)
            assert asyncio.get_running_loop().time() - delivered >= 0.8  # a full quiet period later


async def test_the_header_wins_over_the_query_parameter(
    api: Api, runtime_database_url: str
) -> None:
    async for live in serve(api, runtime_database_url, stream_overlap_seconds=0):
        run = make_run_ids()
        sent = [await send(api, run, n) for n in (1, 2, 3)]
        got = await events_of(
            live,
            run,
            1,
            headers={"Last-Event-ID": sent[2]["event_id"]},
            last_event_id=sent[0]["event_id"],
        )
        assert [m["id"] for m in got] == [sent[2]["event_id"]]
        # and nothing before the header's event follows it
        async with live.open(
            run["run_id"],
            headers={"Last-Event-ID": sent[2]["event_id"]},
            last_event_id=sent[0]["event_id"],
        ) as response:
            stream = messages(response)
            await take(stream, 1)
            with pytest.raises(TimeoutError):
                await take(stream, 1, seconds=0.8)


async def test_idle_streams_send_keepalive_comments(api: Api, runtime_database_url: str) -> None:
    async for live in serve(api, runtime_database_url, stream_keepalive_seconds=0.3):
        run = make_run_ids()
        await send(api, run, 1)
        async with live.open(run["run_id"]) as response:

            async def two_keepalives() -> int:
                count = 0
                async for m in messages(response):
                    count += m.get("comment") == "keepalive"
                    if count == 2:
                        break
                return count

            assert await asyncio.wait_for(two_keepalives(), 5) == 2


async def test_a_stream_closes_itself_at_its_maximum_lifetime(
    api: Api, runtime_database_url: str
) -> None:
    async for live in serve(api, runtime_database_url, stream_max_lifetime_seconds=0.6):
        run = make_run_ids()
        await send(api, run, 1)
        async with live.open(run["run_id"]) as response:
            seen = [m async for m in messages(response)]
        assert any(str(m.get("comment", "")).startswith("max-lifetime") for m in seen)


# ------------------------------------------------------------------ limits and resources


async def test_stream_limits_apply_per_key_and_per_server_and_free_up_on_close(
    api: Api, runtime_database_url: str
) -> None:
    async for live in serve(api, runtime_database_url, stream_max_per_key=2, stream_max_total=3):
        run = make_run_ids()
        await send(api, run, 1)
        a, b = live.open(run["run_id"]), live.open(run["run_id"])
        async with a as ra, b as rb:
            assert ra.status_code == rb.status_code == 200
            async with live.open(run["run_id"]) as third:  # same key, over its limit
                await third.aread()
                body = error(third, 429, "STREAM_LIMIT")
                assert body["retryable"] is True and third.headers["retry-after"] == "5"
                assert body["details"]["scope"] == "key"
            async with live.open(run["run_id"], token="wide_reader") as other_key:
                assert other_key.status_code == 200  # a different key still fits (3 of 3)
                async with live.open(run["run_id"], token="wide") as over_total:
                    await over_total.aread()
                    assert error(over_total, 429, "STREAM_LIMIT")["details"]["scope"] == "server"
        for _ in range(50):  # closing the clients frees every slot
            if live.app.state.streams.limiter.open_streams == 0:
                break
            await asyncio.sleep(0.1)
        assert live.app.state.streams.limiter.open_streams == 0
        for _ in range(50):  # and the hub forgets them: no subscriber outlives its stream
            if live.app.state.streams.hub.subscriber_count == 0:
                break
            await asyncio.sleep(0.1)
        assert live.app.state.streams.hub.subscriber_count == 0
        assert live.app.state.streams.hub.watched_runs == 0
        async with live.open(run["run_id"]) as again:
            assert again.status_code == 200


async def test_an_idle_stream_holds_no_database_connection(live: Live) -> None:
    run = make_run_ids()
    await send(live.api, run, 1)
    async with live.open(run["run_id"]) as response:
        stream = messages(response)
        await take(stream, 1)
        await asyncio.sleep(0.6)  # several idle fallback polls
        assert live.app.state.engine.pool.checkedout() == 0


async def test_a_database_failure_mid_stream_is_reported_in_band_and_ends_the_stream(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = make_run_ids()
    await send(live.api, run, 1)
    calls = 0
    real = EventQueries.arrived_since

    async def flaky(self: EventQueries, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ConnectionError("database went away")
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(EventQueries, "arrived_since", flaky)
    async with live.open(run["run_id"]) as response:
        seen = [m async for m in messages(response) if "event" in m]
    assert [m["event"] for m in seen] == ["trace_event", "error"]
    body = seen[-1]["data"]["error"]
    assert body["code"] == "STREAM_UNAVAILABLE" and body["retryable"] is True
    assert "database went away" not in str(seen[-1])


async def test_a_client_that_stops_reading_is_dropped_and_its_cleanup_runs() -> None:
    async def endless() -> AsyncIterator[bytes]:
        while True:
            yield b": x\n\n"

    closed: list[bool] = []
    response = SseResponse(endless(), write_timeout=0.2, on_close=lambda: closed.append(True))
    sent: list[Any] = []

    async def send_that_blocks_after_the_start(message: Any) -> None:
        sent.append(message["type"])
        if message["type"] == "http.response.body":
            await asyncio.sleep(60)  # the socket buffer is full and the client never reads

    async def receive() -> Any:
        await asyncio.sleep(60)

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET", "headers": []}
    with pytest.raises(ClientDisconnect):  # a write timeout is an OSError: treated as a disconnect
        await asyncio.wait_for(response(scope, receive, send_that_blocks_after_the_start), 5)
    assert closed == [True]
    assert httpx  # imported for the type of live fixtures above


# ------------------------------------------------------------------ polling cost and correctness


async def test_a_resume_after_the_terminal_event_still_ends_the_stream(live: Live) -> None:
    """The terminal event is outside the overlap window: only the database knows the run is over."""
    run = make_run_ids()
    await send(live.api, run, 1, event_type="run.completed")
    live.api.clock.now += timedelta(seconds=120)
    late = await send(live.api, run, 2)  # a late event, long after completion
    async with live.open(run["run_id"], last_event_id=late["event_id"]) as response:
        seen = [m async for m in messages(response) if "event" in m]
    assert [m["event"] for m in seen] == ["trace_event", "run_end"]


async def test_a_late_committed_event_with_an_older_arrival_time_is_found_by_the_window_check(
    live: Live,
) -> None:
    run = make_run_ids()
    first = await send(live.api, run, 1)
    async with live.open(run["run_id"]) as response:
        stream = messages(response)
        await take(stream, 1)
        newer = await send(live.api, run, 2)  # arrives at +1 s
        await take(stream, 1, seconds=3)
        live.api.clock.now -= timedelta(
            seconds=10
        )  # a request that stamped its time earlier commits now
        late = await send(live.api, run, 3)
        got = await take(stream, 1, seconds=3)
    assert got[0]["id"] == late["event_id"] and first["event_id"] != newer["event_id"]


async def test_steady_state_polls_read_after_the_position_and_not_the_whole_window(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    real = EventQueries.arrived_since

    async def spy(self: EventQueries, run_id: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real(self, run_id, **kwargs)

    monkeypatch.setattr(EventQueries, "arrived_since", spy)
    run = make_run_ids()
    for n in range(1, 40):
        await send(live.api, run, n)
    async with live.open(run["run_id"]) as response:
        stream = messages(response)
        await take(stream, 39)
        calls.clear()
        await send(live.api, run, 40)
        await take(stream, 1, seconds=3)
        await asyncio.sleep(0.6)  # let whole poll cycles finish, including the window check
    assert calls, "the stream polled"
    assert all(
        c["since"] is None and c["after"] is not None for c in calls
    )  # index range scans only


async def test_polls_have_a_floor_even_under_a_burst_of_wakeups(
    api: Api, runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async for live in serve(api, runtime_database_url, stream_min_poll_seconds=0.4):
        calls = 0
        real = EventQueries.arrived_since

        async def counting(self: EventQueries, *args: Any, _real: Any = real, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return await _real(self, *args, **kwargs)

        run = make_run_ids()
        await send(api, run, 1)
        async with live.open(run["run_id"]) as response:
            stream = messages(response)
            await take(stream, 1)
            monkeypatch.setattr(EventQueries, "arrived_since", counting)
            for n in range(2, 22):  # twenty wake-ups within a moment
                await send(api, run, n)
            await take(stream, 20, seconds=5)
            await asyncio.sleep(0.1)
        assert calls <= 8, calls  # about one poll per 0.4 s, not one per wake-up


async def test_streams_share_a_small_database_budget_and_all_still_complete(
    api: Api, runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async for live in serve(api, runtime_database_url, stream_db_concurrency=2):
        active = peak = 0
        real = EventQueries.arrived_since

        async def tracked(self: EventQueries, *args: Any, _real: Any = real, **kwargs: Any) -> Any:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.05)
                return await _real(self, *args, **kwargs)
            finally:
                active -= 1

        monkeypatch.setattr(EventQueries, "arrived_since", tracked)
        run = make_run_ids()
        sent = [await send(api, run, n) for n in (1, 2, 3)]

        async def watch(live: Live = live, run: dict[str, str] = run) -> list[str]:
            async with live.open(run["run_id"], token="reader") as response:
                return [m["id"] for m in await take(messages(response), 3)]

        results = await asyncio.gather(*[watch() for _ in range(8)])  # 8 streams, 2 at a time
        assert all(r == [e["event_id"] for e in sent] for r in results)
        assert peak <= 2, peak
