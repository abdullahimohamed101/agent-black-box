"""The query API end to end: events in through the public API, state out through the public API."""

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.ordering import sort_events

from abb_api.runs.summary import derive_run
from tests.api_fixtures import Api
from tests.ingest_helpers import T0, make_run_ids, wire_event
from tests.test_summarizer import scenario

TENANT_FIELDS = ("workspace_id", "project_id", "received_at")


def wire(event: Event) -> dict[str, Any]:
    return {k: v for k, v in event.to_wire().items() if k not in TENANT_FIELDS}


def error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code and body["request_id"] == response.headers["x-request-id"]
    return body  # type: ignore[no-any-return]


async def ingest_scenario(api: Api, *, token: str = "writer") -> tuple[dict[str, str], list[Event]]:
    """Send the standard 13-event run in shuffled, gzip-compressed, overlapping batches."""
    run = make_run_ids()
    events = scenario(api.tenant, run, project="alpha")
    rng = random.Random(3)
    arrival = events[:]
    rng.shuffle(arrival)
    for batch in (arrival[:5], arrival[3:9], arrival[8:], arrival[:2]):  # overlaps = retries
        response = await api.post_batch([wire(e) for e in batch], token=token, compress=True)
        assert response.status_code == 202, response.text
    await api.drain()
    return run, events


async def many_runs(api: Api, count: int, *, project: str = "writer", offset: int = 0) -> list[str]:
    """`count` single-event runs whose start times are distinct and increasing."""
    ids = []
    for i in range(count):
        run = make_run_ids()
        ids.append(run["run_id"])
        response = await api.post_batch([wire_event(run, offset + i + 1)], token=project)
        assert response.json()["accepted"] == 1
    await api.drain()
    return ids


# ------------------------------------------------------------------ acceptance: reconstruction


async def test_a_run_sent_in_shuffled_gzip_batches_is_reconstructed_exactly(api: Api) -> None:
    run, events = await ingest_scenario(api)
    run_id = run["run_id"]
    derived = derive_run(events)

    detail = (await api.get(f"/v1/runs/{run_id}")).json()
    assert detail["status"] == "SUCCESS" and detail["name"] == "Fix bug"
    assert detail["project_id"] == api.tenant.projects["alpha"]
    assert detail["summary_state"] == "current" and detail["ordering_mode"] == "sequence"
    assert detail["summary"] == derived.summary
    assert detail["duration_ms"] == derived.duration_ms == 11000.0

    page = (await api.get(f"/v1/runs/{run_id}/events", limit=500)).json()
    assert [e["event_id"] for e in page["items"]] == [e.event_id for e in sort_events(events)]
    assert page["next_cursor"] is None and page["ordering_mode"] == "sequence"
    assert all("payload" not in e or e["payload"] is None for e in page["items"])

    spans = (await api.get(f"/v1/runs/{run_id}/spans")).json()["items"]
    assert {s["id"] for s in spans} == set(derived.spans)
    for s in spans:
        expected = derived.spans[s["id"]]
        assert (s["parent_span_id"], s["name"], s["status"], s["event_count"]) == (
            expected.parent_span_id, expected.name,
            expected.status.value if expected.status else None, expected.event_count,
        )  # fmt: skip
    names = {s["name"]: s for s in spans if s["name"]}
    assert names["shell"]["parent_span_id"] == names["llm"]["id"] if "llm" in names else True
    github, root = names["github"], next(s for s in spans if s["kind"] == "agent")
    assert github["parent_span_id"] == root["id"] and github["duration_ms"] == 1000


async def test_run_detail_reports_processing_until_the_worker_catches_up(api: Api) -> None:
    run = make_run_ids()
    await api.post_batch([wire_event(run, 1, event_type="run.started", attributes={}, span_id=...)])
    before = (await api.get(f"/v1/runs/{run['run_id']}")).json()
    assert before["summary_state"] == "processing" and before["summary"] == {}
    assert (await api.get(f"/v1/runs/{run['run_id']}/spans")).json()["items"] == []
    await api.drain()
    after = (await api.get(f"/v1/runs/{run['run_id']}")).json()
    assert after["summary_state"] == "current" and after["summary"]["event_count"] == 1


# ------------------------------------------------------------------ creating runs


async def test_creating_a_run_is_idempotent_and_events_then_fill_it_in(api: Api) -> None:
    run_id = new_id(IdKind.RUN)
    body = {"run_id": run_id, "name": "Planned run", "agent_id": "coder", "metadata": {"issue": 42}}
    created = await api.client.post("/v1/runs", json=body, headers=api.headers("writer"))
    assert created.status_code == 201
    assert created.json()["status"] == "QUEUED" and created.json()["metadata"] == {"issue": 42}
    again = await api.client.post("/v1/runs", json=body, headers=api.headers("writer"))
    assert again.status_code == 200 and again.json()["id"] == run_id
    fetched = (await api.get(f"/v1/runs/{run_id}")).json()
    assert fetched["status"] == "QUEUED" and fetched["agent_id"] == "coder"

    await api.post_batch(
        [wire_event({"run_id": run_id, "trace_id": created.json()["trace_id"]}, 1)]
    )
    await api.drain()
    done = (await api.get(f"/v1/runs/{run_id}")).json()
    assert done["status"] == "RUNNING" and done["name"] == "Planned run"  # explicit name kept
    assert done["metadata"] == {"issue": 42}


async def test_creating_a_run_validates_and_authorizes(api: Api) -> None:
    async def post(body: dict[str, Any], token: str = "writer") -> Any:
        return await api.client.post("/v1/runs", json=body, headers=api.headers(token))

    assert (await post({"run_id": "nope"})).status_code == 422
    assert (await post({"name": "x" * 300})).status_code == 422
    assert (await post({"name": "a\x00b"})).status_code == 422
    assert (await post({"metadata": {"k": "a\x00"}})).status_code == 422
    assert (await post({"metadata": {"blob": "x" * 20_000}})).status_code == 422
    deep: dict[str, Any] = {}
    for _ in range(20):
        deep = {"n": deep}
    assert (await post({"metadata": deep})).status_code == 422
    error(await post({}, "reader"), 403, "INSUFFICIENT_SCOPE")
    error(await post({}, "wide"), 403, "PROJECT_KEY_REQUIRED")
    error(await post({}, "garbage"), 401, "API_KEY_INVALID")
    mine = (await post({})).json()["id"]
    error(await post({"run_id": mine}, "beta"), 409, "RUN_PROJECT_MISMATCH")  # other project's key


# ------------------------------------------------------------------ listing runs


async def test_runs_page_without_gaps_or_repeats_and_stay_stable_while_new_runs_arrive(
    api: Api,
) -> None:
    created = await many_runs(api, 25)
    first = (await api.get("/v1/runs", limit=10)).json()
    assert [r["id"] for r in first["items"]] == created[::-1][:10]  # newest first
    await many_runs(api, 5, offset=100)  # newer runs appear between page requests
    second = (await api.get("/v1/runs", limit=10, cursor=first["next_cursor"])).json()
    third = (await api.get("/v1/runs", limit=10, cursor=second["next_cursor"])).json()
    paged = [r["id"] for p in (first, second, third) for r in p["items"]]
    assert paged == created[::-1]  # exactly the original 25, in order, no repeats
    assert third["next_cursor"] is None


async def test_runs_can_be_listed_oldest_first(api: Api) -> None:
    created = await many_runs(api, 6)
    page = (await api.get("/v1/runs", sort="started_at", limit=4)).json()
    nxt = (await api.get("/v1/runs", sort="started_at", limit=4, cursor=page["next_cursor"])).json()
    assert [r["id"] for r in page["items"] + nxt["items"]] == created


async def test_run_filters(api: Api) -> None:
    done, running, other_agent = make_run_ids(), make_run_ids(), make_run_ids()
    await api.post_batch([
        wire_event(done, 1, event_type="run.started", attributes={}, span_id=...),
        wire_event(done, 2, event_type="run.completed", attributes={}, span_id=...),
        wire_event(running, 10, event_type="run.started", attributes={}, span_id=...),
        wire_event(other_agent, 20, agent_id="reviewer"),
    ])  # fmt: skip
    await api.post_batch([wire_event(make_run_ids(), 5)], token="beta")
    await api.drain()

    def ids(response: Any) -> set[str]:
        return {r["id"] for r in response.json()["items"]}

    assert ids(await api.get("/v1/runs", status="SUCCESS")) == {done["run_id"]}
    both = await api.get("/v1/runs", status=["SUCCESS", "RUNNING"], agent_id="coding-agent")
    assert ids(both) == {done["run_id"], running["run_id"]}
    assert ids(await api.get("/v1/runs", agent_id="reviewer")) == {other_agent["run_id"]}
    cut = (T0 + timedelta(seconds=9)).isoformat()
    assert ids(await api.get("/v1/runs", started_after=cut)) == {
        running["run_id"],
        other_agent["run_id"],
    }
    assert ids(await api.get("/v1/runs", started_before=cut)) == {done["run_id"]}
    # a project key sees its own project only; a workspace-wide key sees both, and can filter
    assert len(ids(await api.get("/v1/runs"))) == 3
    assert len(ids(await api.get("/v1/runs", token="wide_reader"))) == 4
    beta = api.tenant.projects["beta"]
    assert len(ids(await api.get("/v1/runs", token="wide_reader", project_id=beta))) == 1


async def test_listing_rejects_bad_parameters(api: Api) -> None:
    for params in (
        {"limit": 0}, {"limit": 201}, {"status": "DONE"}, {"sort": "name"},
        {"agent_id": "Bad Agent"}, {"project_id": "prj_nope"}, {"started_after": "yesterday"},
    ):  # fmt: skip
        assert (await api.get("/v1/runs", **params)).status_code == 422, params
    error(await api.get("/v1/runs", started_after="2026-10-07T12:00:00"), 422, "REQUEST_INVALID")
    for cursor in ("garbage", "e30", "", "x" * 600):
        assert (await api.get("/v1/runs", cursor=cursor)).status_code in (400, 422)
    error(await api.get("/v1/runs", cursor="garbage"), 400, "CURSOR_INVALID")


async def test_a_cursor_from_another_listing_is_rejected(api: Api) -> None:
    run, _ = await ingest_scenario(api)
    spans = (await api.get(f"/v1/runs/{run['run_id']}/spans", limit=2)).json()
    # a spans cursor has exactly the shape of a runs cursor: only its kind tells them apart
    error(await api.get("/v1/runs", cursor=spans["next_cursor"]), 400, "CURSOR_INVALID")
    events = (await api.get(f"/v1/runs/{run['run_id']}/events", limit=2)).json()
    error(await api.get("/v1/runs", cursor=events["next_cursor"]), 400, "CURSOR_INVALID")


async def test_a_project_key_cannot_probe_other_projects(api: Api) -> None:
    beta = api.tenant.projects["beta"]
    error(await api.get("/v1/runs", project_id=beta), 404, "PROJECT_NOT_FOUND")
    other_tenant = api.other.projects["p"]
    error(
        await api.get("/v1/runs", token="wide_reader", project_id=other_tenant),
        404,
        "PROJECT_NOT_FOUND",
    )


# ------------------------------------------------------------------ events


async def test_event_pages_follow_canonical_order_whatever_the_arrival_order(api: Api) -> None:
    run, events = await ingest_scenario(api)
    expected = [e.event_id for e in sort_events(events)]
    got: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = (await api.get(f"/v1/runs/{run['run_id']}/events", limit=4, cursor=cursor)).json()
        got += [e["event_id"] for e in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert got == expected and pages == 4


async def test_runs_without_sequences_are_ordered_by_time(api: Api) -> None:
    run = make_run_ids()
    events = [wire_event(run, n, sequence=...) for n in (5, 1, 3, 2, 4)]
    await api.post_batch(events)
    await api.drain()
    detail = (await api.get(f"/v1/runs/{run['run_id']}")).json()
    assert detail["ordering_mode"] == "time"
    page = (await api.get(f"/v1/runs/{run['run_id']}/events")).json()
    by_time = sorted(events, key=lambda e: e["occurred_at"])
    assert [e["event_id"] for e in page["items"]] == [e["event_id"] for e in by_time]


async def test_time_ordering_is_used_when_any_event_lacks_a_sequence(api: Api) -> None:
    """Skewed sequences: by sequence b, a; by time (c has none) a, b, c."""
    run = make_run_ids()
    a = wire_event(run, 1, sequence=2)  # earlier clock time, later sequence number
    b = wire_event(run, 2, sequence=1)
    c = wire_event(run, 3, sequence=...)
    await api.post_batch([c, b, a])
    await api.drain()
    page = (await api.get(f"/v1/runs/{run['run_id']}/events")).json()
    assert page["ordering_mode"] == "time"
    assert [e["event_id"] for e in page["items"]] == [a["event_id"], b["event_id"], c["event_id"]]
    only_sequenced = make_run_ids()
    x, y = wire_event(only_sequenced, 1, sequence=2), wire_event(only_sequenced, 2, sequence=1)
    await api.post_batch([x, y])
    await api.drain()
    seq_page = (await api.get(f"/v1/runs/{only_sequenced['run_id']}/events")).json()
    assert seq_page["ordering_mode"] == "sequence"
    assert [e["event_id"] for e in seq_page["items"]] == [y["event_id"], x["event_id"]]


async def test_a_late_unsequenced_event_makes_old_cursors_stale(api: Api) -> None:
    run, _ = await ingest_scenario(api)
    path = f"/v1/runs/{run['run_id']}/events"
    first = (await api.get(path, limit=3)).json()
    await api.post_batch([wire_event(run, 99, sequence=...)])
    await api.drain()  # the run flips from sequence to time ordering
    error(await api.get(path, limit=3, cursor=first["next_cursor"]), 409, "CURSOR_STALE")
    restarted = (await api.get(path, limit=3)).json()
    assert restarted["ordering_mode"] == "time"


async def test_event_filters(api: Api) -> None:
    run, events = await ingest_scenario(api)
    path = f"/v1/runs/{run['run_id']}/events"

    def types(response: Any) -> list[str]:
        return [e["event_type"] for e in response.json()["items"]]

    assert set(types(await api.get(path, event_type="retry.attempted"))) == {"retry.attempted"}
    both = types(await api.get(path, event_type=["retry.attempted", "file.modified"]))
    assert sorted(both) == ["file.modified", "retry.attempted"]
    errors = await api.get(path, status="error")
    assert [e["status"] for e in errors.json()["items"]] == ["error"]
    span = next(e for e in events if e.event_type == "agent.started").span_id
    assert set(types(await api.get(path, span_id=span))) == {"agent.started", "agent.completed"}
    assert (await api.get(path, span_id="nope")).status_code == 422


async def test_payloads_are_withheld_from_lists_and_returned_by_the_detail_endpoint(
    api: Api,
) -> None:
    run = make_run_ids()
    event = wire_event(run, 1, payload={"prompt": "hello", "n": [1, 2]})
    plain = wire_event(run, 2)
    await api.post_batch([event, plain])
    await api.drain()
    listed = (await api.get(f"/v1/runs/{run['run_id']}/events")).json()["items"]
    by_id = {e["event_id"]: e for e in listed}
    assert by_id[event["event_id"]]["payload"] is None and by_id[event["event_id"]]["has_payload"]
    assert not by_id[plain["event_id"]]["has_payload"]
    detail = (await api.get(f"/v1/runs/{run['run_id']}/events/{event['event_id']}")).json()
    assert detail["payload"] == {"prompt": "hello", "n": [1, 2]} and detail["has_payload"]
    assert detail["attributes"] == event["attributes"] and detail["received_at"]


async def test_event_detail_lookups_are_scoped_to_their_run(api: Api) -> None:
    run_a, run_b = make_run_ids(), make_run_ids()
    event = wire_event(run_a, 1)
    await api.post_batch([event, wire_event(run_b, 1)])
    await api.drain()
    error(
        await api.get(f"/v1/runs/{run_b['run_id']}/events/{event['event_id']}"),
        404,
        "EVENT_NOT_FOUND",
    )
    error(await api.get(f"/v1/runs/{run_a['run_id']}/events/evt_nope"), 404, "EVENT_NOT_FOUND")


# ------------------------------------------------------------------ spans


async def test_spans_page_stably(api: Api) -> None:
    run = make_run_ids()
    events = [
        wire_event(run, n, event_type="tool.call.started", attributes={"tool.name": f"t{n}"})
        for n in range(1, 8)
    ]
    events.append(
        wire_event(
            run,
            8,
            event_type="tool.call.completed",
            attributes={"tool.name": "t1"},
            span_id=events[0]["span_id"],
        )
    )
    await api.post_batch(events)
    await api.drain()
    path = f"/v1/runs/{run['run_id']}/spans"
    everything = (await api.get(path)).json()["items"]
    got: list[str] = []
    cursor = None
    while True:
        page = (await api.get(path, limit=3, cursor=cursor)).json()
        got += [s["id"] for s in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert got == [s["id"] for s in everything] and len(got) == 7
    assert [s["name"] for s in everything] == [f"t{n}" for n in range(1, 8)]  # by start time


async def test_unstarted_spans_sort_last_and_page_correctly(api: Api) -> None:
    run = make_run_ids()
    starts = [
        wire_event(run, n, event_type="tool.call.started", attributes={"tool.name": "s"})
        for n in (1, 2)
    ]
    orphans = [
        wire_event(run, n, event_type="tool.call.completed", attributes={"tool.name": "o"})
        for n in (3, 4, 5)
    ]
    await api.post_batch(starts + orphans)  # the orphans never saw their start events
    await api.drain()
    path = f"/v1/runs/{run['run_id']}/spans"
    got: list[str] = []
    cursor = None
    while True:
        page = (await api.get(path, limit=2, cursor=cursor)).json()
        got += [s["name"] for s in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert got == ["s", "s", "o", "o", "o"]


# ------------------------------------------------------------------ isolation


async def test_other_workspaces_and_projects_get_identical_404s(api: Api) -> None:
    run, events = await ingest_scenario(api)
    run_id, event_id = run["run_id"], events[0].event_id
    routes = [
        f"/v1/runs/{run_id}",
        f"/v1/runs/{run_id}/events",
        f"/v1/runs/{run_id}/events/{event_id}",
        f"/v1/runs/{run_id}/spans",
    ]
    ghost = new_id(IdKind.RUN)
    for route in routes:
        unknown = route.replace(run_id, ghost)
        baseline = await api.get(unknown, token="reader")
        for token in ("other", "beta"):  # another workspace; another project of the same one
            response = await api.get(route, token=token)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == baseline.json()["error"]["code"]
            assert response.json()["error"]["message"] == baseline.json()["error"]["message"]
        assert (
            await api.get(route, token="wide_reader")
        ).status_code == 200  # workspace key sees it
    assert (await api.get("/v1/runs", token="other")).json()["items"] == []
    assert run_id not in {
        r["id"] for r in (await api.get("/v1/runs", token="beta")).json()["items"]
    }


async def test_every_read_route_demands_authentication_and_the_read_scope(api: Api) -> None:
    run, events = await ingest_scenario(api)
    routes = [
        "/v1/runs",
        f"/v1/runs/{run['run_id']}",
        f"/v1/runs/{run['run_id']}/events",
        f"/v1/runs/{run['run_id']}/events/{events[0].event_id}",
        f"/v1/runs/{run['run_id']}/spans",
    ]
    for route in routes:
        error(await api.get(route, token=None), 401, "API_KEY_INVALID")
        error(await api.get(route, token="revoked"), 401, "API_KEY_INVALID")
        error(await api.get(route, token="ingest_only"), 403, "INSUFFICIENT_SCOPE")
        assert (await api.get(route, token="reader")).status_code == 200


async def test_malformed_ids_are_plain_404s(api: Api) -> None:
    for bad in ("nope", "run_", "evt_01J9ZZZZZZZZZZZZZZZZZZZZZZ", "RUN_X", "a" * 200):
        error(await api.get(f"/v1/runs/{bad}"), 404, "RUN_NOT_FOUND")


# ------------------------------------------------------------------ failures


async def test_a_database_outage_during_a_read_is_a_retryable_503(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from abb_api.runs.queries import RunQueries

    async def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise OperationalError("select", {}, Exception("server closed the connection"))

    monkeypatch.setattr(RunQueries, "page", boom)
    response = await api.get("/v1/runs")
    body = error(response, 503, "DEPENDENCY_UNAVAILABLE")
    assert body["retryable"] is True and response.headers["retry-after"] == "2"


async def test_stored_timestamps_round_trip_with_utc_offsets(api: Api) -> None:
    run = make_run_ids()
    await api.post_batch([wire_event(run, 1, occurred_at="2026-10-07T14:00:00+02:00")])
    await api.drain()
    item = (await api.get(f"/v1/runs/{run['run_id']}/events")).json()["items"][0]
    assert datetime.fromisoformat(item["occurred_at"]) == datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


async def test_the_same_run_id_in_two_workspaces_never_mixes_events_or_spans(api: Api) -> None:
    run = make_run_ids()
    mine = [wire_event(run, n) for n in (1, 2, 3)]
    theirs = [wire_event(run, n) for n in (1, 2)]
    await api.post_batch(mine)
    await api.post_batch(theirs, token="other")
    await api.drain()
    path = f"/v1/runs/{run['run_id']}"
    assert {e["event_id"] for e in (await api.get(f"{path}/events")).json()["items"]} == {
        e["event_id"] for e in mine
    }
    their_view = await api.get(f"{path}/events", token="other")
    assert {e["event_id"] for e in their_view.json()["items"]} == {e["event_id"] for e in theirs}
    assert (await api.get(path)).json()["summary"]["event_count"] == 3
    assert (await api.get(path, token="other")).json()["summary"]["event_count"] == 2
    assert len((await api.get(f"{path}/spans")).json()["items"]) == 3
    mine_detail = await api.get(f"{path}/events/{theirs[0]['event_id']}")
    assert mine_detail.status_code == 404  # their event id does not exist in my workspace


async def test_event_lists_never_select_the_payload_column(api: Api) -> None:
    import re

    from sqlalchemy import event as sa_event

    run = make_run_ids()
    await api.post_batch([wire_event(run, 1, payload={"secret": "x" * 1000})])
    await api.drain()
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    app_engine = api.app.state.engine.sync_engine  # the engine the application itself uses
    sa_event.listen(app_engine, "before_cursor_execute", record)
    try:
        await api.get(f"/v1/runs/{run['run_id']}/events")
    finally:
        sa_event.remove(app_engine, "before_cursor_execute", record)
    event_selects = [s for s in statements if "FROM events" in s]
    assert event_selects
    for statement in event_selects:
        mentions = re.findall(r"events\.payload(?![_\w])( IS NOT NULL)?", statement)
        assert mentions == [" IS NOT NULL"], statement  # only the has_payload flag, never the body
