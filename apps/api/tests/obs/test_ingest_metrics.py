"""Ingestion and artifact metrics: each moves by exactly the expected amount, with its labels."""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id

from abb_api.artifacts.store import ArtifactStoreError, LocalFsArtifactStore
from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.obs.catalogue import Observability
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.helpers import observations, total, value


async def test_a_committed_batch_counts_events_bytes_one_ack_and_one_duration(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    run = make_run_ids()
    body = json.dumps({"events": [wire_event(run, 1), wire_event(run, 2)]}).encode()
    response = await api.client.post("/v1/events/batch", content=body, headers=api.headers())
    assert response.status_code == 202
    assert value(obs, "telemetry_events_received_total") == 2
    assert value(obs, "telemetry_bytes_received_total") == len(body)
    assert value(obs, "ingest_acks_total", outcome="committed") == 1
    assert total(obs, "ingest_acks_total") == 1
    assert observations(obs, "ingest_ack_duration_seconds") == 1
    assert total(obs, "telemetry_events_rejected_total") == 0
    # A replay of the same batch is committed again but accepts nothing new.
    again = await api.client.post("/v1/events/batch", content=body, headers=api.headers())
    assert again.json()["duplicates"] == 2
    assert value(obs, "telemetry_events_received_total") == 2
    assert value(obs, "ingest_acks_total", outcome="committed") == 2
    assert observations(obs, "ingest_ack_duration_seconds") == 2


async def test_an_invalid_event_inside_an_accepted_batch_is_counted_per_event(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    run = make_run_ids()
    broken = wire_event(run, 3, occurred_at="not a time")
    response = await api.post_batch([wire_event(run, 1), broken, {"nonsense": True}])
    assert response.status_code == 202 and response.json()["rejected"] == 2
    codes = sorted(e["code"] for e in response.json()["errors"])
    assert codes == ["EVENT_INVALID", "EVENT_SCHEMA_UNSUPPORTED"], codes
    assert value(obs, "telemetry_events_received_total") == 1
    assert value(obs, "telemetry_events_rejected_total", reason="invalid") == 1
    assert value(obs, "telemetry_events_rejected_total", reason="schema_unsupported") == 1
    assert total(obs, "telemetry_events_rejected_total") == 2
    assert value(obs, "ingest_acks_total", outcome="committed") == 1


async def test_a_reserved_event_type_is_its_own_reason(observed: tuple[Api, Observability]) -> None:
    api, obs = observed
    run = make_run_ids()
    response = await api.post_batch([wire_event(run, 1, event_type="policy.decision")])
    assert response.status_code == 202
    assert value(obs, "telemetry_events_rejected_total", reason="reserved") == 1


async def test_request_level_refusals_count_once_with_their_reason(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    # 400: an empty batch.
    assert (await api.post_batch([])).status_code == 400
    assert value(obs, "telemetry_events_rejected_total", reason="invalid") == 1
    assert value(obs, "ingest_acks_total", outcome="rejected") == 1
    # 415: not JSON.
    bad = await api.client.post(
        "/v1/events/batch", content=b"{}", headers={**api.headers(), "content-type": "text/plain"}
    )
    assert bad.status_code == 415
    assert value(obs, "telemetry_events_rejected_total", reason="invalid") == 2
    # 413: over the body limit.
    huge = b'{"events":[' + b" " * (5 * 1024 * 1024 + 10) + b"]}"
    assert (
        await api.client.post("/v1/events/batch", content=huge, headers=api.headers())
    ).status_code == 413
    assert value(obs, "telemetry_events_rejected_total", reason="too_large") == 1
    # 403: a workspace-wide key cannot ingest.
    assert (await api.post_batch([wire_event(make_run_ids(), 1)], token="wide")).status_code == 403
    assert value(obs, "telemetry_events_rejected_total", reason="unauthorized") == 1
    assert value(obs, "ingest_acks_total", outcome="rejected") == 4
    assert value(obs, "ingest_acks_total", outcome="committed") == 0
    assert value(obs, "ingest_acks_total", outcome="error") == 0
    assert observations(obs, "ingest_ack_duration_seconds") == 4
    assert value(obs, "telemetry_events_received_total") == 0
    assert value(obs, "telemetry_bytes_received_total") == 0


async def test_a_bad_gzip_body_is_an_invalid_refusal(observed: tuple[Api, Observability]) -> None:
    api, obs = observed
    response = await api.client.post(
        "/v1/events/batch",
        content=gzip.compress(b"not json")[:-4],
        headers=api.headers(**{"content-encoding": "gzip"}),
    )
    assert response.status_code == 400
    assert value(obs, "telemetry_events_rejected_total", reason="invalid") == 1
    assert value(obs, "ingest_acks_total", outcome="rejected") == 1


async def test_a_commit_that_fails_is_an_error_ack_not_a_committed_one(
    observed: tuple[Api, Observability], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, obs = observed
    from abb_api.ingestion import store as event_store

    async def lost(*_: Any, **__: Any) -> None:
        raise OSError("connection reset")  # the database went away before the commit

    monkeypatch.setattr(event_store.PgEventStore, "ingest", lost)
    response = await api.post_batch([wire_event(make_run_ids(), 1)])
    assert response.status_code == 503
    assert value(obs, "ingest_acks_total", outcome="error") == 1
    assert value(obs, "ingest_acks_total", outcome="committed") == 0
    assert value(obs, "telemetry_events_rejected_total", reason="unavailable") == 1
    assert value(obs, "telemetry_events_received_total") == 0
    assert observations(obs, "ingest_ack_duration_seconds") == 1

    async def bug(*_: Any, **__: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(event_store.PgEventStore, "ingest", bug)
    assert (await api.post_batch([wire_event(make_run_ids(), 1)])).status_code == 500
    assert value(obs, "ingest_acks_total", outcome="error") == 2
    assert observations(obs, "ingest_ack_duration_seconds") == 2


async def test_the_single_event_endpoint_counts_one_ack_and_never_double_counts(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    run = make_run_ids()
    ok = await api.client.post("/v1/events", json=wire_event(run, 1), headers=api.headers())
    assert ok.status_code == 202
    assert value(obs, "telemetry_events_received_total") == 1
    assert value(obs, "ingest_acks_total", outcome="committed") == 1
    bad = await api.client.post(
        "/v1/events", json=wire_event(run, 2, occurred_at="not a time"), headers=api.headers()
    )
    assert bad.status_code == 422
    assert value(obs, "telemetry_events_rejected_total", reason="invalid") == 1
    assert total(obs, "telemetry_events_rejected_total") == 1, "counted once, not twice"
    assert value(obs, "ingest_acks_total", outcome="rejected") == 1
    notjson = await api.client.post("/v1/events", content=b"{{", headers=api.headers())
    assert notjson.status_code == 422
    assert value(obs, "telemetry_events_rejected_total", reason="invalid") == 2
    assert observations(obs, "ingest_ack_duration_seconds") == 3


async def test_authentication_failures_do_not_reach_the_ingestion_counters(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    assert (await api.post_batch([wire_event(make_run_ids(), 1)], token=None)).status_code == 401
    assert total(obs, "ingest_acks_total") == 0
    assert total(obs, "telemetry_events_rejected_total") == 0


# ------------------------------------------------------------------ artifacts


async def _artifact_writer(api: Api) -> str:
    async with api.engine.begin() as conn:
        keys = ApiKeyRepository(conn, api.tenant.context)
        key = await keys.create(
            scopes=frozenset({scopes.ARTIFACTS_WRITE}),
            project_id=api.tenant.project_uuids["alpha"],
        )
    return key.token


def _put(api: Api, token: str, body: bytes, **headers: str) -> Any:
    artifact = new_artifact_id()
    return api.client.put(
        f"/v1/artifacts/{artifact}",
        params={"run_id": new_id(IdKind.RUN)},
        content=body,
        headers={"authorization": f"Bearer {token}", "content-type": "text/plain", **headers},
    )


def new_artifact_id() -> str:
    return new_id(IdKind.ARTIFACT)


async def test_a_stored_artifact_observes_its_size_once(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    token = await _artifact_writer(api)
    response = await _put(api, token, b"hello artifact")
    assert response.status_code == 201
    assert observations(obs, "artifact_upload_bytes") == 1
    assert total(obs, "artifact_upload_failures_total") == 0


async def test_artifact_failures_carry_a_closed_reason(
    observed: tuple[Api, Observability], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, obs = observed
    token = await _artifact_writer(api)
    wrong = await _put(api, token, b"abc", **{"x-content-sha256": "0" * 64})
    assert wrong.status_code == 422
    assert value(obs, "artifact_upload_failures_total", reason="invalid") == 1
    too_big = await _put(api, token, b"x" * 5000)
    assert too_big.status_code == 413
    assert value(obs, "artifact_upload_failures_total", reason="too_large") == 1

    async def refuse(self: LocalFsArtifactStore, *_: Any) -> None:
        raise ArtifactStoreError("disk")

    monkeypatch.setattr(LocalFsArtifactStore, "put", refuse)
    down = await _put(api, token, b"fine")
    assert down.status_code == 503
    assert value(obs, "artifact_upload_failures_total", reason="store_unavailable") == 1
    assert total(obs, "artifact_upload_failures_total") == 3
    assert observations(obs, "artifact_upload_bytes") == 0
