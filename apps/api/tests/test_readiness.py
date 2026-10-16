"""/readyz (Phase 19c step 3, ADR-169): real dependency state, fixed reason codes, cheap to hit."""

import asyncio
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.health import readiness
from abb_api.health.readiness import CodeRevisions, Readiness, judge_revision
from abb_api.jobs.outbox import SUMMARIZE_RUN, OutboxRepository
from abb_api.main import create_app
from abb_api.obs.catalogue import build_observability
from tests.conftest import UNREACHABLE_DATABASE_URL, _client, make_settings
from tests.ingest_helpers import make_tenant
from tests.obs.helpers import value


async def ready_body(client: httpx.AsyncClient) -> tuple[int, dict[str, Any]]:
    response = await client.get("/readyz")
    return response.status_code, response.json()


def checks(body: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    found = body["checks"]
    assert isinstance(found, dict)
    return found


@pytest.fixture(autouse=True)
def _no_cache_between_tests() -> Iterator[None]:
    readiness.code_revisions.cache_clear()
    yield
    readiness.code_revisions.cache_clear()


async def test_everything_healthy_is_ready_and_the_runtime_role_can_read_the_revision(
    runtime_database_url: str, tmp_path: Path
) -> None:
    app = create_app(make_settings(runtime_database_url, artifact_dir=str(tmp_path / "store")))
    async for client in _client(app):
        response = await client.get("/readyz")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ready"
    assert checks(body) == {
        "database": {"status": "ok", "reason": None},
        "schema": {"status": "ok", "reason": None},
        "artifacts": {"status": "ok", "reason": None},
        "role": {"status": "ok", "reason": None},
        "worker": {"status": "ok", "reason": None},
        "retention": {"status": "ok", "reason": "dry_run"},
    }
    assert list((tmp_path / "store").iterdir()) == []  # the probe file is removed


async def test_a_database_behind_the_code_is_not_ready(
    runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        readiness, "code_revisions", lambda: CodeRevisions("9999", frozenset({"0106", "9999"}))
    )
    async for client in _client(create_app(make_settings(runtime_database_url))):
        status, body = await ready_body(client)
    assert status == 503
    assert body["status"] == "not_ready"
    assert checks(body)["schema"] == {"status": "fail", "reason": "behind"}
    assert checks(body)["database"]["status"] == "ok"


async def test_a_database_ahead_of_the_code_is_ready_with_a_warning(
    runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A rolling deploy migrates first: the old processes must keep serving until replaced.
    monkeypatch.setattr(readiness, "code_revisions", lambda: CodeRevisions("0001", frozenset()))
    async for client in _client(create_app(make_settings(runtime_database_url))):
        status, body = await ready_body(client)
    assert status == 200
    assert checks(body)["schema"] == {"status": "degraded", "reason": "ahead"}


async def test_unfindable_migrations_degrade_the_schema_check_instead_of_failing(
    runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "code_revisions", lambda: None)
    async for client in _client(create_app(make_settings(runtime_database_url))):
        status, body = await ready_body(client)
    assert status == 200
    assert checks(body)["schema"] == {"status": "degraded", "reason": "head_unknown"}


def test_judge_revision_cases() -> None:
    code = CodeRevisions("0106", frozenset({"0105", "0106"}))
    assert judge_revision("0106", code).status == "ok"
    assert judge_revision("0105", code).reason == "behind"
    assert judge_revision(None, code).reason == "behind"
    assert judge_revision("0200", code).reason == "ahead"


async def test_the_code_head_is_the_real_alembic_head() -> None:
    revisions = readiness.code_revisions()
    assert revisions is not None
    assert revisions.head == "0106"
    assert {"0001", "0104"} <= revisions.known


async def test_the_artifact_directory_must_exist_and_be_writable(
    runtime_database_url: str, tmp_path: Path
) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("x")
    app = create_app(make_settings(runtime_database_url, artifact_dir=str(blocked / "store")))
    async for client in _client(app):
        status, body = await ready_body(client)
    assert status == 503
    assert checks(body)["artifacts"] == {"status": "fail", "reason": "missing"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
async def test_a_read_only_artifact_directory_is_not_writable(
    runtime_database_url: str, tmp_path: Path
) -> None:
    store = tmp_path / "ro"
    store.mkdir()
    store.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        app = create_app(make_settings(runtime_database_url, artifact_dir=str(store)))
        async for client in _client(app):
            status, body = await ready_body(client)
    finally:
        store.chmod(stat.S_IRWXU)
    assert status == 503
    assert checks(body)["artifacts"] == {"status": "fail", "reason": "not_writable"}
    assert str(tmp_path) not in json.dumps(body)  # a path is never in the answer


async def test_a_late_job_degrades_the_worker_line_without_failing_readiness(
    engine: AsyncEngine, runtime_database_url: str, tmp_path: Path
) -> None:
    tenant = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue(
            job_type=SUMMARIZE_RUN, dedupe_key="late", payload={}
        )
        await conn.execute(text("UPDATE outbox_jobs SET available_at = now() - interval '10 min'"))
    app = create_app(make_settings(runtime_database_url, artifact_dir=str(tmp_path / "s")))
    async for client in _client(app):
        status, body = await ready_body(client)
    assert status == 200
    assert checks(body)["worker"] == {"status": "degraded", "reason": "worker_lag"}


# ------------------------------------------------------------------ cost and flapping


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_concurrent_probes_share_one_evaluation_and_the_cache_expires(
    runtime_database_url: str, tmp_path: Path
) -> None:
    clock = Clock()
    app = create_app(make_settings(runtime_database_url, artifact_dir=str(tmp_path / "s")))
    async for client in _client(app):
        probe = Readiness(
            lambda: app.state.engine,
            app.state.settings,
            artifacts_dir=str(tmp_path / "s"),
            monotonic=clock,
        )
        reports = await asyncio.gather(*(probe.evaluate() for _ in range(25)))
        assert probe.evaluations == 1
        assert all(r is reports[0] for r in reports)
        await probe.evaluate()
        assert probe.evaluations == 1  # still cached
        clock.now += 2.5
        await probe.evaluate()
        assert probe.evaluations == 2
        assert (await client.get("/healthz")).status_code == 200


async def test_gauges_per_check_and_flaps(
    runtime_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    obs = build_observability()
    clock = Clock()
    app = create_app(
        make_settings(runtime_database_url, artifact_dir=str(tmp_path / "s")), observability=obs
    )
    async for _ in _client(app):
        probe = Readiness(
            lambda: app.state.engine,
            app.state.settings,
            artifacts_dir=str(tmp_path / "s"),
            metrics=obs.catalogue,
            monotonic=clock,
            ttl_seconds=0,
            artifact_ttl_seconds=0,
        )
        await probe.evaluate()
        assert value(obs, "readiness_check", check="artifacts") == 1
        assert value(obs, "readiness_flaps_total") == 0
        monkeypatch.setattr(
            readiness, "probe_directory", lambda _: readiness.CheckResult("fail", "not_writable")
        )
        report = await probe.evaluate()
        assert not report.ready
        assert value(obs, "readiness_check", check="artifacts") == 0
        assert value(obs, "readiness_check", check="database") == 1
        assert value(obs, "readiness_flaps_total") == 1
        monkeypatch.undo()
        await probe.evaluate()
        assert value(obs, "readiness_flaps_total") == 2


async def test_not_ready_is_answered_with_retry_after_and_no_error_text(
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(UNREACHABLE_DATABASE_URL, artifact_dir=str(tmp_path / "s")))
    async for client in _client(app):
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    text_ = response.text
    for forbidden in ("asyncpg", "Errno", "127.0.0.1", "Traceback", str(tmp_path)):
        assert forbidden not in text_
    reasons = {c["reason"] for c in response.json()["checks"].values()}
    assert reasons <= {None, "down", "unchecked", "unverified", "dry_run"}
