"""POST /v1/events and /v1/events/batch through the real application and database."""

import asyncio
import gzip
import json
import logging
import random
import tracemalloc
import zlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.errors import AppError
from abb_api.core.logging import JsonFormatter
from abb_api.db import tables as t
from abb_api.ingestion.body import decode_body
from abb_api.ingestion.ratelimit import InMemoryRateLimiter
from abb_api.ingestion.store import PgEventStore
from tests.api_fixtures import Api, build_api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event

BATCH = "/v1/events/batch"


async def count(engine: AsyncEngine, table: Any) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(select(func.count()).select_from(table))).scalar_one())


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    return error  # type: ignore[no-any-return]


# ------------------------------------------------------------------ authentication


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "Bearer garbage",
        "bearer abb_live_" + "z" * 12 + "." + "A" * 43,  # well-formed, unknown key
    ],
)
async def test_missing_or_bad_credentials_get_one_uniform_401(
    api: Api, authorization: str | None
) -> None:
    headers = {"content-type": "application/json"}
    if authorization is not None:
        headers["authorization"] = authorization
    body = json.dumps({"events": [wire_event(make_run_ids(), 1)]}).encode()
    response = await api.client.post(BATCH, content=body, headers=headers)
    error = assert_error(response, 401, "API_KEY_INVALID")
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert error["details"] == {}
    assert await count(api.engine, t.events) == 0


@pytest.mark.parametrize("name", ["revoked", "expired"])
async def test_revoked_and_expired_keys_look_like_any_other_bad_key(api: Api, name: str) -> None:
    response = await api.post_batch([wire_event(make_run_ids(), 1)], token=name)
    bad = await api.post_batch([wire_event(make_run_ids(), 1)], token="garbage")
    assert response.status_code == bad.status_code == 401
    first, second = response.json()["error"], bad.json()["error"]
    assert {k: first[k] for k in ("code", "message")} == {k: second[k] for k in ("code", "message")}


async def test_wrong_secret_for_a_real_key_is_rejected(api: Api) -> None:
    token = api.tokens["writer"]
    forged = token[: token.index(".") + 1] + "A" * 43
    assert_error(
        await api.post_batch([wire_event(make_run_ids(), 1)], token=forged), 401, "API_KEY_INVALID"
    )


async def test_a_key_without_the_write_scope_gets_403_naming_the_scope(api: Api) -> None:
    error = assert_error(
        await api.post_batch([wire_event(make_run_ids(), 1)], token="reader"),
        403,
        "INSUFFICIENT_SCOPE",
    )
    assert error["details"] == {
        "required_scope": "events:write",
        "required_permission": "event.write",
    }


async def test_a_workspace_wide_key_cannot_ingest(api: Api) -> None:
    assert_error(
        await api.post_batch([wire_event(make_run_ids(), 1)], token="wide"),
        403,
        "PROJECT_KEY_REQUIRED",
    )
    assert await count(api.engine, t.events) == 0


# ------------------------------------------------------------------ the happy path


async def test_a_valid_batch_is_accepted_and_stored(api: Api) -> None:
    run = make_run_ids()
    events = [wire_event(run, n) for n in range(1, 6)]
    response = await api.post_batch(events, batch_id="batch-1", sent_at="2026-10-07T12:00:00Z")
    assert response.status_code == 202
    body = response.json()
    assert body["batch_id"] == "batch-1"
    assert (body["accepted"], body["duplicates"], body["conflicts"], body["rejected"]) == (
        5,
        0,
        0,
        0,
    )
    assert body["errors"] == [] and body["request_id"] == response.headers["x-request-id"]
    assert body["server_time"] == "2026-10-07T12:05:00Z"  # from the injected clock
    assert await count(api.engine, t.events) == 5
    async with api.engine.connect() as conn:
        row = (
            await conn.execute(
                select(t.events.c.workspace_id, t.events.c.project_id, t.events.c.received_at)
            )
        ).first()
    assert (
        row is not None and row.received_at == api.clock.now
    )  # tenant and time are server-assigned


async def test_gzip_batches_are_accepted(api: Api) -> None:
    response = await api.post_batch(
        [wire_event(make_run_ids(), n) for n in range(3)], compress=True
    )
    assert response.status_code == 202 and response.json()["accepted"] == 3


async def test_retrying_a_batch_is_safe(api: Api) -> None:
    events = [wire_event(make_run_ids(), n) for n in range(4)]
    first = await api.post_batch(events)
    again = await api.post_batch(events)
    assert first.json()["accepted"] == 4
    assert (again.json()["accepted"], again.json()["duplicates"]) == (0, 4)
    assert await count(api.engine, t.events) == 4


async def test_same_id_with_different_content_is_reported_as_a_conflict(api: Api) -> None:
    run = make_run_ids()
    original = wire_event(run, 1, status="success")
    await api.post_batch([original])
    response = await api.post_batch([{**original, "status": "error"}])
    assert response.status_code == 202 and response.json()["conflicts"] == 1
    async with api.engine.connect() as conn:
        assert (await conn.execute(select(t.events.c.status))).scalar_one() == "success"


async def test_late_events_after_run_completion_are_accepted(api: Api) -> None:
    run = make_run_ids()
    await api.post_batch([wire_event(run, 1, event_type="run.completed", attributes={})])
    late = await api.post_batch([wire_event(run, 2)])
    assert late.json()["accepted"] == 1


# ------------------------------------------------------------------ partial rejection


async def test_invalid_events_are_reported_individually_and_the_rest_are_stored(api: Api) -> None:
    run = make_run_ids()
    good_a, good_b = wire_event(run, 1), wire_event(run, 4)
    events: list[Any] = [
        good_a,
        wire_event(run, 2, event_type="Not.Valid"),
        wire_event(run, 3, schema_version="2.0"),
        good_b,
        wire_event(run, 5, workspace_id=api.other.workspace_id),  # claims another tenant
        wire_event(run, 6, attributes={"tool.name": 5}),
        "not even an object",
        wire_event(run, 7, payload={"blob": "x" * 70_000}),
    ]
    response = await api.post_batch(events)
    assert response.status_code == 202
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (2, 6)
    by_index = {e["index"]: e for e in body["errors"]}
    assert sorted(by_index) == [1, 2, 4, 5, 6, 7]
    assert by_index[2]["code"] == "EVENT_SCHEMA_UNSUPPORTED"
    assert any(i["code"] == "tenant_mismatch" for i in by_index[4]["issues"])
    assert by_index[1]["event_id"] == events[1]["event_id"]
    assert by_index[6]["event_id"] is None  # not an object: nothing to echo
    assert await count(api.engine, t.events) == 2


async def test_error_entries_never_echo_submitted_values(api: Api) -> None:
    secret = "sk-live-TOPSECRET-123"
    response = await api.post_batch(
        [wire_event(make_run_ids(), 1, event_type=secret, attributes={secret: secret})]
    )
    assert response.status_code == 202
    assert "TOPSECRET" not in response.text


async def test_an_event_cannot_borrow_a_run_of_another_project(api: Api) -> None:
    run = make_run_ids()
    # a second project's key creates the run first (via the store); the writer's project differs
    from abb_api.ingestion.store import PgEventStore
    from tests.ingest_helpers import build_event

    async with api.engine.begin() as conn:
        await PgEventStore(conn, api.tenant.context).ingest(
            [build_event(api.tenant, run, n=1, project="beta")]
        )
    response = await api.post_batch([wire_event(run, 2), wire_event(make_run_ids(), 3)])
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert body["errors"][0]["code"] == "RUN_PROJECT_MISMATCH"


async def test_using_another_tenants_run_id_never_touches_their_data(api: Api) -> None:
    run = make_run_ids()
    first = await api.post_batch([wire_event(run, 1)])
    assert first.json()["accepted"] == 1
    theirs = await api.post_batch([wire_event(run, 2)], token="other")
    assert (
        theirs.json()["accepted"] == 1
    )  # their own namespace; no hint that the id exists elsewhere
    async with api.engine.connect() as conn:
        rows = (
            await conn.execute(select(t.events.c.workspace_id).where(t.events.c.sequence == 1))
        ).all()
    assert len(rows) == 1


# ------------------------------------------------------------------ envelope, limits, encodings


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "BATCH_INVALID"),
        (b"{", "BATCH_INVALID"),
        (b"[]", "BATCH_INVALID"),
        (b'"x"', "BATCH_INVALID"),
        (b"{}", "BATCH_INVALID"),
        (b'{"events": {}}', "BATCH_INVALID"),
        (b'{"events": []}', "BATCH_INVALID"),
        (b'{"events": [{}], "batch_id": 5}', "BATCH_INVALID"),
        (b'{"events": [{}], "batch_id": "has spaces"}', "BATCH_INVALID"),
    ],
)
async def test_malformed_envelopes_are_a_400(api: Api, body: bytes, code: str) -> None:
    response = await api.client.post(BATCH, content=body, headers=api.headers())
    assert_error(response, 400, code)


async def test_too_many_events_in_a_batch(database_url: str, engine: AsyncEngine) -> None:
    settings = make_settings(database_url, ingest_max_batch_events=3, rate_limit_burst_events=3)
    async for api in build_api(database_url, engine, settings=settings):
        events = [wire_event(make_run_ids(), n) for n in range(4)]
        error = assert_error(await api.post_batch(events), 400, "BATCH_INVALID")
        assert error["details"] == {"max_events": 3}
        assert (await api.post_batch(events[:3])).status_code == 202


async def test_content_type_and_encoding_are_enforced(api: Api) -> None:
    body = json.dumps({"events": [wire_event(make_run_ids(), 1)]}).encode()
    wrong_type = await api.client.post(
        BATCH, content=body, headers={**api.headers(), "content-type": "text/plain"}
    )
    assert_error(wrong_type, 415, "UNSUPPORTED_MEDIA_TYPE")
    with_charset = await api.client.post(
        BATCH,
        content=body,
        headers={**api.headers(), "content-type": "application/json; charset=utf-8"},
    )
    assert with_charset.status_code == 202
    brotli = await api.client.post(
        BATCH, content=body, headers=api.headers(**{"content-encoding": "br"})
    )
    assert_error(brotli, 415, "UNSUPPORTED_ENCODING")
    identity = await api.client.post(
        BATCH, content=body, headers=api.headers(**{"content-encoding": "identity"})
    )
    assert identity.status_code == 202


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        (b"this is not gzip", "garbage"),
        (gzip.compress(b'{"events": [1]}')[:-8], "truncated"),
        (gzip.compress(b'{"events": [1]}') + b"junk", "trailing data"),
        (zlib.compress(b'{"events": [1]}'), "zlib, not gzip"),
    ],
)
async def test_broken_gzip_is_a_400(api: Api, payload: bytes, label: str) -> None:
    response = await api.client.post(
        BATCH, content=payload, headers=api.headers(**{"content-encoding": "gzip"})
    )
    assert_error(response, 400, "BATCH_INVALID")


async def test_size_limits_apply_to_compressed_and_decompressed_bytes(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(database_url, ingest_max_body_bytes=2048)
    async for api in build_api(database_url, engine, settings=settings):
        big = b'{"events": [], "pad": "' + b"x" * 5000 + b'"}'
        # declared Content-Length over the limit
        assert_error(
            await api.client.post(BATCH, content=big, headers=api.headers()),
            413,
            "PAYLOAD_TOO_LARGE",
        )

        async def chunks(data: bytes) -> AsyncIterator[bytes]:  # no Content-Length: streamed
            for start in range(0, len(data), 512):
                yield data[start : start + 512]

        streamed = await api.client.post(BATCH, content=chunks(big), headers=api.headers())
        assert_error(streamed, 413, "PAYLOAD_TOO_LARGE")

        bomb = gzip.compress(b'{"events": [], "pad": "' + b"\x00" * 1_000_000 + b'"}')
        assert len(bomb) < 2048  # tiny on the wire, huge when expanded
        response = await api.client.post(
            BATCH, content=bomb, headers=api.headers(**{"content-encoding": "gzip"})
        )
        assert_error(response, 413, "PAYLOAD_TOO_LARGE")


# ------------------------------------------------------------------ single event endpoint


async def test_single_event_endpoint(api: Api) -> None:
    event = wire_event(make_run_ids(), 1)
    accepted = await api.client.post("/v1/events", content=json.dumps(event), headers=api.headers())
    assert accepted.status_code == 202
    assert (
        accepted.json()["status"] == "accepted" and accepted.json()["event_id"] == event["event_id"]
    )
    again = await api.client.post("/v1/events", content=json.dumps(event), headers=api.headers())
    assert again.json()["status"] == "duplicate"
    clash = await api.client.post(
        "/v1/events", content=json.dumps({**event, "status": "error"}), headers=api.headers()
    )
    assert clash.status_code == 202 and clash.json()["status"] == "conflict"


@pytest.mark.parametrize(
    ("mutation", "status", "code"),
    [
        ({"event_type": "Bad"}, 422, "EVENT_INVALID"),
        ({"schema_version": "9.0"}, 400, "EVENT_SCHEMA_UNSUPPORTED"),
        ({"payload": {"blob": "y" * 70_000}}, 422, "EVENT_INVALID"),
    ],
)
async def test_single_event_errors(
    api: Api, mutation: dict[str, Any], status: int, code: str
) -> None:
    event = wire_event(make_run_ids(), 1, **mutation)
    error = assert_error(
        await api.client.post("/v1/events", content=json.dumps(event), headers=api.headers()),
        status,
        code,
    )
    assert error["details"]["issues"]


async def test_single_event_not_json(api: Api) -> None:
    assert_error(
        await api.client.post("/v1/events", content=b"nope", headers=api.headers()),
        422,
        "EVENT_INVALID",
    )


async def test_get_is_not_allowed_on_ingestion_routes(api: Api) -> None:
    response = await api.client.get(BATCH, headers=api.headers())
    assert response.status_code == 405 and response.json()["error"]["code"] == "HTTP_405"


# ------------------------------------------------------------------ rate limiting


async def test_rate_limit_returns_429_with_retry_after_then_recovers(
    database_url: str, engine: AsyncEngine
) -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(
        events_per_second=2, burst_events=4, bytes_per_second=10**9, burst_bytes=10**9,
        monotonic=lambda: now[0],
    )  # fmt: skip
    settings = make_settings(
        database_url,
        ingest_max_batch_events=4,
        rate_limit_burst_events=4,
        rate_limit_events_per_second=2,
    )
    async for api in build_api(database_url, engine, settings=settings, rate_limiter=limiter):
        batch = [wire_event(make_run_ids(), n) for n in range(4)]
        assert (await api.post_batch(batch)).status_code == 202
        blocked = await api.post_batch([wire_event(make_run_ids(), 9)])
        error = assert_error(blocked, 429, "RATE_LIMITED")
        assert error["retryable"] is True
        assert blocked.headers["retry-after"] == "1"
        assert await count(engine, t.events) == 4  # nothing from the rejected request was stored
        now[0] += 1.0  # one second refills two events
        assert (await api.post_batch([wire_event(make_run_ids(), 10)])).status_code == 202


async def test_one_projects_rate_limit_does_not_throttle_another() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(
        events_per_second=1,
        burst_events=2,
        bytes_per_second=1000,
        burst_bytes=1000,
        monotonic=lambda: now[0],
    )
    assert limiter.acquire("a", events=2, bytes_=10) is None
    assert limiter.acquire("a", events=1, bytes_=10) is not None
    assert limiter.acquire("b", events=2, bytes_=10) is None


def test_limiter_never_partially_consumes_and_bounds_memory() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(
        events_per_second=10, burst_events=10, bytes_per_second=100, burst_bytes=100,
        monotonic=lambda: now[0], max_keys=20,
    )  # fmt: skip
    assert limiter.acquire("p", events=1, bytes_=500) == pytest.approx(4.0)  # bytes are the limit
    assert limiter.acquire("p", events=10, bytes_=100) is None  # events were not consumed above
    for i in range(100):
        limiter.acquire(f"project-{i}", events=1, bytes_=1)
    assert len(limiter._buckets) <= 20


def test_burst_smaller_than_a_maximal_batch_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="burst"):
        make_settings("x", rate_limit_burst_events=10, ingest_max_batch_events=1000)


# ------------------------------------------------------------------ dependency failures


async def test_database_outage_is_a_retryable_503_not_a_401_or_500(
    client_db_down: Any,
) -> None:
    token = "abb_live_" + "a" * 12 + "." + "B" * 43
    response = await client_db_down.post(
        BATCH,
        content=json.dumps({"events": [wire_event(make_run_ids(), 1)]}),
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"},
    )
    error = assert_error(response, 503, "DEPENDENCY_UNAVAILABLE")
    assert error["retryable"] is True and response.headers["retry-after"] == "2"


# ------------------------------------------------------------------ log hygiene


async def test_logs_contain_neither_credentials_nor_event_content(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    secret_text = "PAYLOAD-SECRET-sk-live-0123456789"
    run = make_run_ids()
    token = api.tokens["writer"]
    with caplog.at_level(logging.DEBUG):
        await api.post_batch(
            [
                wire_event(
                    run, 1, payload={"prompt": secret_text}, attributes={"tool.name": secret_text}
                )
            ]
        )
        await api.post_batch([wire_event(run, 2, event_type=secret_text)])  # rejected event
        await api.post_batch([wire_event(run, 3)], token="garbage")  # rejected key
    formatter = JsonFormatter("test")
    rendered = "\n".join(formatter.format(r) for r in caplog.records)
    secret = token.split(".", 1)[1]
    assert secret not in rendered and token not in rendered
    assert "PAYLOAD-SECRET" not in rendered
    request_lines = [
        json.loads(formatter.format(r)) for r in caplog.records if r.name == "abb_api.request"
    ]
    ingest_line = next(
        line for line in request_lines if line["path"] == BATCH and line["status"] == 202
    )
    assert ingest_line["workspace_id"].startswith("ws_") and ingest_line["actor_id"].startswith(
        "key:"
    )
    assert ingest_line["project_id"].startswith("prj_")  # safe identifiers are present


# ------------------------------------------------------------------ robustness


async def test_mutated_batches_never_cause_a_server_error(api: Api) -> None:
    rng = random.Random(7)
    junk: list[Any] = [
        None,
        True,
        -1,
        2**53,
        1.5,
        "",
        "x" * 5000,
        "a\x00b",
        [],
        {},
        {"a": {"b": 1}},
        "evt_x",
    ]
    for _ in range(150):
        run = make_run_ids()
        events: list[Any] = []
        for n in range(rng.randint(1, 6)):
            event = wire_event(run, n)
            for _ in range(rng.randint(0, 3)):
                event[rng.choice([*event, "attributes", "payload", "tags"])] = rng.choice(junk)
            events.append(event if rng.random() > 0.1 else rng.choice(junk))
        response = await api.post_batch(events, compress=rng.random() < 0.3)
        assert response.status_code in (202, 400), (response.status_code, response.text[:300])
        assert response.headers["content-type"].startswith("application/json")


async def test_concurrent_requests_with_the_same_events_store_them_once(api: Api) -> None:
    events = [wire_event(make_run_ids(), n) for n in range(10)]
    responses = await asyncio.gather(*[api.post_batch(events) for _ in range(20)])
    assert all(r.status_code == 202 for r in responses)
    assert sum(r.json()["accepted"] for r in responses) == 10
    assert await count(api.engine, t.events) == 10
    assert new_id(IdKind.EVENT)  # ids still generate


# ------------------------------------------------------------------ failures after authentication


def _failing_store(error: BaseException) -> Any:
    async def ingest(self: Any, events: Any) -> Any:
        raise error

    return ingest


class _FakeDriverError(Exception):
    sqlstate = "40P01"  # deadlock_detected


@pytest.mark.parametrize(
    "error",
    [
        OSError("connection reset"),
        TimeoutError(),
        OperationalError("stmt", {}, Exception("server closed the connection")),
        DBAPIError("stmt", {}, _FakeDriverError("deadlock detected")),
    ],
)
async def test_storage_trouble_after_auth_is_a_retryable_503(
    api: Api, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    monkeypatch.setattr(PgEventStore, "ingest", _failing_store(error))
    response = await api.post_batch([wire_event(make_run_ids(), 1)])
    error_body = assert_error(response, 503, "DEPENDENCY_UNAVAILABLE")
    assert error_body["retryable"] is True and response.headers["retry-after"] == "2"


async def test_an_unexpected_bug_is_a_generic_500_without_internals(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        PgEventStore, "ingest", _failing_store(RuntimeError("secret-internal-detail"))
    )
    response = await api.post_batch([wire_event(make_run_ids(), 1)])
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "secret-internal-detail" not in response.text


def test_gzip_expansion_is_capped_while_decompressing_not_after() -> None:
    """A 100 MB expansion must not be materialised just to be rejected (memory, not just size)."""
    limit = 1024 * 1024
    bomb = gzip.compress(b"\x00" * 100 * 1024 * 1024)
    assert len(bomb) < limit
    tracemalloc.start()
    try:
        with pytest.raises(AppError) as exc:
            decode_body(bomb, "gzip", limit)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert exc.value.status_code == 413
    assert peak < 16 * 1024 * 1024, f"peak memory {peak} bytes: the bomb was expanded"


async def test_extreme_timestamps_are_rejected_per_event_not_as_a_server_error(api: Api) -> None:
    """Found by independent verification: 0001-01-01T00:00:00+14:00 overflowed UTC conversion."""
    good = wire_event(make_run_ids(), 1)
    events = [
        good,
        wire_event(make_run_ids(), 2, occurred_at="0001-01-01T00:00:00+14:00"),
        wire_event(make_run_ids(), 3, occurred_at="9999-12-31T23:59:59-14:00"),
    ]
    response = await api.post_batch(events)
    assert response.status_code == 202
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (1, 2)
    assert {e["issues"][0]["code"] for e in body["errors"]} == {"timestamp_out_of_range"}


async def test_an_unexpected_crash_while_validating_one_event_rejects_only_that_event(
    api: Api, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A poison event must not become a retryable 500 that an SDK would resend forever."""
    from abb_event_schema.parse import parse_event_in as real

    poison = wire_event(make_run_ids(), 2)

    def flaky(raw: Any) -> Any:
        if isinstance(raw, dict) and raw.get("event_id") == poison["event_id"]:
            raise OverflowError("a bug in validation")
        return real(raw)

    monkeypatch.setattr("abb_api.ingestion.service.parse_event_in", flaky)
    events = [wire_event(make_run_ids(), 1), poison, wire_event(make_run_ids(), 3)]
    with caplog.at_level(logging.ERROR):
        response = await api.post_batch(events)
    assert response.status_code == 202
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (2, 1)
    assert body["errors"][0]["index"] == 1 and body["errors"][0]["event_id"] == poison["event_id"]
    assert "a bug in validation" not in response.text  # internals stay in the server log
    assert any("event validation crashed" in r.getMessage() for r in caplog.records)
