"""Artifact upload and chunked reads through the public API (ADR-030), on real PostgreSQL."""

import gzip
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.service import align_to_character
from abb_api.artifacts.store import ArtifactStoreError, LocalFsArtifactStore
from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from tests.api_fixtures import Api, build_api
from tests.conftest import make_settings

MAX_BYTES = 4096
RUN = new_id(IdKind.RUN)


@pytest.fixture
async def art(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[Api]:
    settings = make_settings(
        database_url, artifact_dir=str(tmp_path / "store"), artifact_max_bytes=MAX_BYTES
    )
    async for instance in build_api(database_url, engine, settings=settings):
        async with engine.begin() as conn:
            keys = ApiKeyRepository(conn, instance.tenant.context)
            alpha, beta = (
                instance.tenant.project_uuids["alpha"],
                instance.tenant.project_uuids["beta"],
            )
            write = frozenset({scopes.ARTIFACTS_WRITE})
            instance.tokens["art_alpha"] = (await keys.create(scopes=write, project_id=alpha)).token
            instance.tokens["art_beta"] = (await keys.create(scopes=write, project_id=beta)).token
            instance.tokens["art_wide"] = (await keys.create(scopes=write)).token
            other_keys = ApiKeyRepository(conn, instance.other.context)
            instance.tokens["art_other"] = (
                await other_keys.create(scopes=write, project_id=instance.other.project_uuids["p"])
            ).token
        yield instance


def new_artifact_id() -> str:
    return new_id(IdKind.ARTIFACT)


async def put(
    api: Api,
    artifact_id: str,
    body: bytes,
    *,
    token: str | None = "art_alpha",
    run_id: str = RUN,
    content_type: str = "text/plain",
    compress: bool = False,
    **query: Any,
) -> httpx.Response:
    headers = {"content-type": content_type}
    if token is not None:
        headers["authorization"] = f"Bearer {api.tokens.get(token, token)}"
    sha = query.pop("sha", None)
    if sha is not None:
        headers["x-content-sha256"] = sha
    if compress:
        body, headers["content-encoding"] = gzip.compress(body), "gzip"
    params = {"run_id": run_id, "kind": "stdout", **query}
    return await api.client.put(
        f"/v1/artifacts/{artifact_id}", content=body, params=params, headers=headers
    )


def error_code(response: httpx.Response, status: int) -> str:
    assert response.status_code == status, response.text
    return str(response.json()["error"]["code"])


async def test_upload_stores_bytes_with_a_verified_hash(art: Api, tmp_path: Path) -> None:
    aid, body = new_artifact_id(), b"hello terminal\n"
    sha = hashlib.sha256(body).hexdigest()
    r = await put(art, aid, body, sha=sha, name="pytest stdout")
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["id"] == aid and meta["run_id"] == RUN and meta["kind"] == "stdout"
    assert meta["sha256"] == sha and meta["size_bytes"] == len(body)
    assert meta["project_id"] == art.tenant.projects["alpha"]
    files = [p for p in (tmp_path / "store").rglob("*") if p.is_file()]
    assert [f.read_bytes() for f in files] == [body]
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"


async def test_retrying_an_upload_is_idempotent_and_a_different_body_conflicts(art: Api) -> None:
    aid = new_artifact_id()
    assert (await put(art, aid, b"one")).status_code == 201
    again = await put(art, aid, b"one")
    assert again.status_code == 200 and again.json()["sha256"] == hashlib.sha256(b"one").hexdigest()
    assert error_code(await put(art, aid, b"two"), 409) == "ARTIFACT_CONFLICT"
    got = await art.get(f"/v1/artifacts/{aid}/content", token="reader")
    assert got.json()["content"] == "one"  # the original bytes were not replaced


async def test_a_wrong_claimed_hash_is_rejected_and_nothing_is_stored(
    art: Api, tmp_path: Path
) -> None:
    aid = new_artifact_id()
    r = await put(art, aid, b"abc", sha="0" * 64)
    assert error_code(r, 422) == "ARTIFACT_HASH_MISMATCH"
    assert (await art.get(f"/v1/artifacts/{aid}", token="reader")).status_code == 404
    assert not [p for p in (tmp_path / "store").rglob("*") if p.is_file()]


async def test_size_limit_applies_to_the_body_and_to_the_decompressed_body(art: Api) -> None:
    assert (
        error_code(await put(art, new_artifact_id(), b"x" * (MAX_BYTES + 1)), 413)
        == "PAYLOAD_TOO_LARGE"
    )
    bomb = b"a" * (MAX_BYTES * 50)  # tiny when compressed
    assert (
        error_code(await put(art, new_artifact_id(), bomb, compress=True), 413)
        == "PAYLOAD_TOO_LARGE"
    )
    ok = await put(art, new_artifact_id(), b"y" * MAX_BYTES, compress=True)
    assert ok.status_code == 201


@pytest.mark.parametrize(
    ("token", "status"),
    [
        (None, 401),
        ("garbage", 401),
        ("revoked", 401),
        ("reader", 403),  # runs:read only
        ("writer", 403),  # events:write, not artifacts:write
        ("art_wide", 403),  # a workspace-wide key cannot upload
    ],
)
async def test_upload_needs_a_project_bound_artifacts_write_key(
    art: Api, token: str | None, status: int
) -> None:
    assert (await put(art, new_artifact_id(), b"x", token=token)).status_code == status


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"content_type": "text/html"}, 415),
        ({"content_type": "application/x-sh"}, 415),
        ({"run_id": "not-a-run"}, 422),
        ({"kind": "script"}, 422),
        ({"name": "bad\nname"}, 422),
        ({"sha": "xyz"}, 422),
    ],
)
async def test_upload_validates_its_parameters(
    art: Api, kwargs: dict[str, Any], status: int
) -> None:
    assert (await put(art, new_artifact_id(), b"x", **kwargs)).status_code == status


async def test_a_malformed_artifact_id_is_rejected(art: Api) -> None:
    assert (await put(art, "art_nope", b"x")).status_code == 422
    assert (await art.get("/v1/artifacts/art_nope", token="reader")).status_code == 404


async def test_reads_are_scoped_to_the_tenant_and_project(art: Api) -> None:
    aid = new_artifact_id()
    await put(art, aid, b"secret-ish output")
    assert (await art.get(f"/v1/artifacts/{aid}", token="reader")).status_code == 200
    assert (await art.get(f"/v1/artifacts/{aid}", token="wide_reader")).status_code == 200
    # another project's key, another workspace's key, and a key without runs:read never see it
    assert (await art.get(f"/v1/artifacts/{aid}", token="beta")).status_code == 404
    assert (await art.get(f"/v1/artifacts/{aid}/content", token="other")).status_code == 404
    assert (await art.get(f"/v1/artifacts/{aid}", token="art_alpha")).status_code == 403
    assert (await art.get(f"/v1/artifacts/{aid}", token=None)).status_code == 401


async def test_another_workspace_cannot_claim_or_overwrite_an_id(art: Api) -> None:
    aid = new_artifact_id()
    await put(art, aid, b"mine")
    # ids are per workspace: the other tenant stores its own artifact under the same id
    assert (await put(art, aid, b"theirs", token="art_other")).status_code == 201
    mine = await art.get(f"/v1/artifacts/{aid}/content", token="reader")
    theirs = await art.get(f"/v1/artifacts/{aid}/content", token="other")
    assert mine.json()["content"] == "mine" and theirs.json()["content"] == "theirs"


async def test_chunked_reads_cover_the_whole_artifact_once(art: Api) -> None:
    aid = new_artifact_id()
    text = "".join(f"line {i:04d} ünïcödé ✓\n" for i in range(120))  # multi-byte characters
    body = text.encode()
    assert len(body) <= MAX_BYTES
    await put(art, aid, body)
    offset, parts = 0, []
    while True:
        r = await art.get(f"/v1/artifacts/{aid}/content", token="reader", offset=offset, limit=300)
        assert r.status_code == 200, r.text
        chunk = r.json()
        assert chunk["offset"] == offset and chunk["total_bytes"] == len(body)
        assert "�" not in chunk["content"]  # never cut inside a character
        parts.append(chunk["content"])
        if chunk["next_offset"] is None:
            break
        assert chunk["next_offset"] > offset
        offset = chunk["next_offset"]
    assert "".join(parts) == text
    assert len(parts) > 5


async def test_read_limits_and_offsets_are_validated(art: Api) -> None:
    aid = new_artifact_id()
    await put(art, aid, b"abc")
    url = f"/v1/artifacts/{aid}/content"
    assert (await art.get(url, token="reader", limit=10)).status_code == 422
    assert (await art.get(url, token="reader", limit=10_000_000)).status_code == 422
    assert (await art.get(url, token="reader", offset=-1)).status_code == 422
    assert (
        error_code(await art.get(url, token="reader", offset=4), 422) == "ARTIFACT_OFFSET_INVALID"
    )
    end = await art.get(url, token="reader", offset=3)
    assert end.json()["content"] == "" and end.json()["next_offset"] is None


async def test_html_in_an_artifact_is_returned_as_json_data_never_as_a_document(art: Api) -> None:
    aid = new_artifact_id()
    await put(art, aid, b"<script>alert(1)</script>")
    r = await art.get(f"/v1/artifacts/{aid}/content", token="reader")
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["content"] == "<script>alert(1)</script>"


async def test_upload_counts_against_the_project_rate_limit(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    from abb_api.ingestion.ratelimit import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(
        events_per_second=0.001, burst_events=1000, bytes_per_second=1, burst_bytes=10 * 1024 * 1024
    )
    settings = make_settings(
        database_url, artifact_dir=str(tmp_path / "s"), rate_limit_burst_events=1000
    )
    async for api in build_api(database_url, engine, settings=settings, rate_limiter=limiter):
        async with engine.begin() as conn:
            keys = ApiKeyRepository(conn, api.tenant.context)
            token = (
                await keys.create(
                    scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                    project_id=api.tenant.project_uuids["alpha"],
                )
            ).token
        statuses = [
            (await put(api, new_artifact_id(), b"x" * 600_000, token=token)).status_code
            for _ in range(30)
        ]
        assert 429 in statuses  # 30 x 600 kB exceeds the 10 MiB byte bucket


class TestStore:
    async def test_keys_cannot_escape_the_root(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(tmp_path)
        for bad in (
            "../x",
            "a/b",
            "../../etc/passwd",
            "z" * 32 + "/" + "z" * 32 + "-" + "0" * 64,
            "",
        ):
            with pytest.raises(ArtifactStoreError):
                await store.put(bad, b"x")
            with pytest.raises(ArtifactStoreError):
                await store.read(bad, 0, 1)

    async def test_round_trip_and_delete(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(tmp_path)
        key = "a" * 32 + "/" + "b" * 32 + "-" + "c" * 64
        await store.put(key, b"0123456789")
        assert await store.read(key, 2, 3) == b"234"
        await store.delete(key)
        with pytest.raises(ArtifactStoreError):
            await store.read(key, 0, 1)
        await store.delete(key)  # deleting twice is fine


def test_character_alignment() -> None:
    data = "aé".encode()  # a, 0xC3, 0xA9
    assert align_to_character(data, 2) == 1  # a cut between the two bytes moves back
    assert align_to_character(data, 1) == 1
    assert align_to_character(data, 3) == 3


async def test_conflict_responses_do_not_reveal_whether_the_id_is_another_projects(
    art: Api,
) -> None:
    aid = new_artifact_id()
    await put(art, aid, b"alpha content")
    other_project = await put(art, aid, b"anything", token="art_beta")
    different = await put(art, aid, b"different")
    assert other_project.status_code == different.status_code == 409
    assert other_project.json()["error"]["message"] == different.json()["error"]["message"]


async def test_a_run_of_another_project_is_refused(art: Api) -> None:
    run_id = new_id(IdKind.RUN)
    event = {
        "schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": run_id,
        "trace_id": new_id(IdKind.TRACE), "agent_id": "a", "event_type": "run.started",
        "occurred_at": "2026-10-07T10:00:00Z", "sequence": 1, "attributes": {},
    }  # fmt: skip
    assert (await art.post_batch([event], token="beta")).status_code == 202  # run belongs to beta
    refused = await put(art, new_artifact_id(), b"x", run_id=run_id)  # an alpha key
    assert error_code(refused, 409) == "RUN_PROJECT_MISMATCH"
    assert (
        await put(art, new_artifact_id(), b"x", run_id=run_id, token="art_beta")
    ).status_code == 201


async def test_a_row_whose_file_vanished_is_404_not_a_retryable_503(
    art: Api, tmp_path: Path
) -> None:
    aid = new_artifact_id()
    await put(art, aid, b"bytes")
    for f in (tmp_path / "store").rglob("*"):
        if f.is_file():
            f.unlink()
    r = await art.get(f"/v1/artifacts/{aid}/content", token="reader")
    assert error_code(r, 404) == "ARTIFACT_CONTENT_MISSING"
    assert r.json()["error"]["retryable"] is False


async def test_artifact_names_are_short_and_printable(art: Api) -> None:
    assert (await put(art, new_artifact_id(), b"x", name="n" * 128)).status_code == 201
    assert (await put(art, new_artifact_id(), b"x", name="n" * 129)).status_code == 422


async def test_artifacts_do_not_drain_the_event_ingestion_bucket(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    from abb_api.ingestion.ratelimit import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(
        events_per_second=0.001, burst_events=1000, bytes_per_second=1, burst_bytes=10 * 1024 * 1024
    )
    settings = make_settings(database_url, artifact_dir=str(tmp_path / "s"))
    async for api in build_api(database_url, engine, settings=settings, rate_limiter=limiter):
        async with engine.begin() as conn:
            keys = ApiKeyRepository(conn, api.tenant.context)
            token = (
                await keys.create(
                    scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                    project_id=api.tenant.project_uuids["alpha"],
                )
            ).token
        for _ in range(12):  # exhausts the artifact byte bucket
            await put(api, new_artifact_id(), b"x" * 1_000_000, token=token)
        event = {
            "schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": new_id(IdKind.RUN),
            "trace_id": new_id(IdKind.TRACE), "agent_id": "a", "event_type": "run.started",
            "occurred_at": "2026-10-07T10:00:00Z", "sequence": 1, "attributes": {},
        }  # fmt: skip
        assert (await api.post_batch([event])).status_code == 202
