"""INV-5 evidence: realistic and hostile traffic through the real application, worker, retention
and backup tooling, with every logger at DEBUG, leaves no sentinel, secret, address, cookie, key,
e-mail or DSN password in any log record (message, args, extra fields, exception text) or in the
CLIs' output (Phase 19c D4, gate 4).

The sentinels are planted everywhere a client controls bytes: payloads, attributes, agent and
tool names, paths, query strings, headers, cookies, bearer tokens. A control test proves the
detector sees a leak, and another that the pinned libraries would have leaked at DEBUG.
"""

import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.alerts.delivery import deliver_due
from abb_api.alerts.notifiers import build_registry
from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.jobs.outbox import OutboxRepository
from abb_api.jobs.worker import Worker
from abb_api.retention.runner import RetentionRunnerSettings
from abb_api.retention.tick import run_tick
from tests.alerts_helpers import SENTINEL_SIGNING, seed_destination, seed_rule
from tests.alerts_receivers import RESPONSE_SENTINEL, TlsReceiver
from tests.api_fixtures import WEB_ORIGIN, Api, build_api
from tests.backup.conftest import World, world  # noqa: F401 - the fixture under test
from tests.backup.helpers import PASSPHRASE, base_environ, run_cli, write_passphrase
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.log_capture import capture_everything, find_leaks
from tests.retention.enforce_helpers import enforcing
from tests.retention.helpers import NOW, days_ago, seed_run
from tests.test_alerts_delivery import T0, dsettings, pending
from tests.test_auth_throttle import probe, unknown_key, wrong_secret

# Built at run time: a traceback prints source lines (our code), never values, so a literal here
# would show up as "code" and the test would be reporting its own text.
EXC_MESSAGE = "SENTINEL-EXC-MESSAGE-" + "9a47"
FAKE_DSN_PASSWORD = "SENTINELDSN" + "PASS"
ADDRESSES = {"198.51.100.99", "203.0.113.77", "2001:db8::dead:beef"}
SENTINELS = {
    "SENTINEL-PAYLOAD-8c1f",
    "SENTINEL-ATTR-2b7d",
    "SENTINEL-AGENT-91aa",
    "SENTINEL-TOOL-33e0",
    "SENTINEL-PATH-5d52",
    "SENTINEL-QUERY-0c9b",
    "SENTINEL-HEADER-77f4",
    "SENTINEL-COOKIE-a1e6",
    "SENTINEL-COMMAND-4b18",
    "SENTINEL-UA-6e3c",
    "SENTINEL-BEARER-d20f",
    EXC_MESSAGE,
    FAKE_DSN_PASSWORD,
    "SENTINEL-PROMPT-f3b2",
    "victim.sentinel@mail-sentinel.example",
    "bad id SENTINEL-RID-1c8e",
}
KNOWN_SECRETS = {SENTINEL_SIGNING, "SENTINELSECRETTOKEN1234", RESPONSE_SENTINEL, PASSPHRASE}


def dsn_secrets(database_url: str) -> set[str]:
    url = make_url(database_url)
    return {url.password} if url.password else set()


def planted_event(run: dict[str, str], n: int, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "agent_id": "SENTINEL-AGENT-91aa",
        "attributes": {
            "tool.name": "SENTINEL-TOOL-33e0",
            "tool.command": "rm -rf SENTINEL-COMMAND-4b18",
            "note": "mail victim.sentinel@mail-sentinel.example",
        },
        "payload": {"prompt": "SENTINEL-PROMPT-f3b2", "body": "SENTINEL-PAYLOAD-8c1f"},
    }
    return wire_event(run, n, **{**fields, **overrides})


async def hostile_http(api: Api) -> tuple[set[str], set[str]]:
    """Realistic and hostile requests; returns the live secrets used and the error codes seen."""
    codes: set[str] = set()
    headers = {
        "x-forwarded-for": "198.51.100.99",
        "user-agent": "SENTINEL-UA-6e3c",
        "x-sentinel": "SENTINEL-HEADER-77f4",
        "x-request-id": "bad id SENTINEL-RID-1c8e",
        "cookie": "abb_session=SENTINEL-COOKIE-a1e6; theme=SENTINEL-COOKIE-a1e6",
        "origin": WEB_ORIGIN,
    }
    run = make_run_ids()
    # Valid, rejected, quota-refused and malformed ingestion with the sentinels everywhere.
    events = [planted_event(run, n) for n in range(1, 8)]
    events.append(planted_event(run, 9, event_type=EXC_MESSAGE))
    events.append(planted_event(run, 10, occurred_at=EXC_MESSAGE))
    events.append(planted_event(run, 11, payload={"text": "nul \u0000 SENTINEL-PAYLOAD-8c1f"}))
    for path in (
        "/v1/events/batch?q=SENTINEL-QUERY-0c9b",
        "/v1/events/batch?token=SENTINEL-BEARER-d20f",
    ):
        await api.client.post(
            path,
            content=json.dumps({"events": events}).encode(),
            headers={**api.headers(), **{k: v for k, v in headers.items() if k != "cookie"}},
        )
    for single in (planted_event(run, 20), planted_event(run, 21, event_type="x" * 300)):
        await api.client.post(
            "/v1/events",
            json=single,
            headers={**api.headers(), "x-sentinel": "SENTINEL-HEADER-77f4"},
        )
    await api.client.post(  # junk JSON, wrong media type, truncated body
        "/v1/events/batch", content=b'{"events": [SENTINEL-PAYLOAD-8c1f', headers=api.headers()
    )
    await api.client.post(
        "/v1/events/batch",
        content=b"SENTINEL-PAYLOAD-8c1f",
        headers={**api.headers(), "content-type": "text/plain"},
    )
    # Paths and queries a client controls.
    for path in (
        "/v1/runs/run_SENTINEL-PATH-5d52",
        "/v1/runs?q=SENTINEL-QUERY-0c9b&limit=SENTINEL-QUERY-0c9b",
        "/no/such/SENTINEL-PATH-5d52?x=SENTINEL-QUERY-0c9b",
        "/v1/artifacts/SENTINEL-PATH-5d52",
        "/v1/search?q=SENTINEL-QUERY-0c9b",
    ):
        await api.client.get(path, headers={**api.headers("reader"), **headers})
    await api.client.delete("/v1/auth/sessions/SENTINEL-PATH-5d52", headers=headers)
    await api.client.get("/v1/me", headers=headers)
    # An unhandled error whose message holds a sentinel and the path an id.
    await api.client.get("/sentinel-boom/SENTINEL-PATH-5d52", headers=headers)
    # Failed authentication: many keys (the throttles trip), a wrong secret on a real key, garbage
    # bearer values, repeated bad session cookies (the session throttle trips).
    leaks: set[str] = set()
    for _ in range(70):
        token = unknown_key()
        leaks.update({token, token.split(".")[1]})
        await probe(api, token, "203.0.113.77")
    real = wrong_secret(api.tokens["writer"])
    leaks.add(real.split(".")[1])
    for _ in range(6):
        await probe(api, real, "203.0.113.77")
    for bearer in ("SENTINEL-BEARER-d20f", "abb_live_SENTINEL-BEARER-d20f.x", "Basic SENTINEL"):
        await api.client.get("/v1/runs", headers={"authorization": f"Bearer {bearer}"})
    for _ in range(45):
        reply = await api.client.get(
            "/v1/me",
            headers={
                "cookie": f"abb_session={token_urlsafe(32)}",  # well formed, unknown: counted
                "x-forwarded-for": "2001:db8::dead:beef",
            },
        )
        codes.add(reply.json().get("error", {}).get("code", ""))
    for _ in range(6):  # the failed-key throttle (429) must have been reached too
        reply = await probe(api, real, "203.0.113.77")
        codes.add(reply.json().get("error", {}).get("code", ""))
    leaks.update(api.tokens.values())
    leaks.update(t.split(".")[1] for t in api.tokens.values())
    await api.drain()
    return leaks, codes


async def failing_jobs(api: Api) -> None:
    """A job that always raises (message holds a sentinel) and one with no handler."""

    async def explode(job: Any, conn: Any) -> None:
        raise RuntimeError(f"{EXC_MESSAGE} postgresql://u:{FAKE_DSN_PASSWORD}@h/db")

    worker = Worker(
        api.worker._engine,
        {"sentinel_boom": explode},
        make_settings("postgresql+asyncpg://x:y@localhost/z", worker_max_attempts=2),
        owner="sentinel-test",
        metrics=api.app.state.observability.catalogue,
    )
    async with api.engine.begin() as conn:
        outbox = OutboxRepository(conn, api.tenant.context)
        for job_type in ("sentinel_boom", "sentinel_unhandled"):
            await outbox.enqueue(
                job_type=job_type,
                dedupe_key=uuid.uuid4().hex,
                payload={"note": "SENTINEL-PAYLOAD-8c1f"},
            )
    for _ in range(4):
        await worker.run_once()
        async with api.engine.begin() as conn:  # skip the backoff
            await conn.exec_driver_sql(
                "UPDATE outbox_jobs SET available_at = now() - interval '1 s'"
            )


async def alert_failures(api: Api) -> None:
    async with TlsReceiver(responder=lambda _: (500, {})) as receiver:
        config = dsettings(api, alerts_max_attempts=1)
        registry = build_registry(config, receiver.sender())
        destination = await seed_destination(
            api.engine, api.tenant.context, url=receiver.url("/hook/SENTINELSECRETTOKEN1234")
        )
        rule = await seed_rule(api.engine, api.tenant.context, [destination])
        await pending(api, rule, destination, n=3)
        async with api.engine.begin() as conn:
            await deliver_due(
                conn,
                api.tenant.context,
                now=T0,
                settings=config,
                registry=registry,
                rng=lambda: 0.5,
            )

    # And a name that now resolves to an internal address (refused before any connection).
    async def inward(host: str, port: int) -> list[str]:
        return ["169.254.169.254"]

    await pending(api, rule, destination, n=1, at=T0 + timedelta(minutes=1))
    async with TlsReceiver() as other:
        blocked = build_registry(config, other.sender(resolver=inward))
        async with api.engine.begin() as conn:
            await deliver_due(
                conn,
                api.tenant.context,
                now=T0 + timedelta(minutes=1),
                settings=config,
                registry=blocked,
                rng=lambda: 0.5,
            )


async def retention_passes(api: Api, tmp_path: Path) -> list[Any]:
    store = LocalFsArtifactStore(tmp_path / "retention-artifacts")
    await seed_run(
        api.engine, api.tenant, ended=days_ago(4000), artifact_sizes=(64, 128), store=store
    )
    results = []
    for cfg in (
        RetentionRunnerSettings(retention_mode="dry_run"),
        enforcing(),
        enforcing(retention_batch_size=1),
    ):
        results.append(
            await run_tick(
                api.engine, cfg, budget_seconds=60, keep_reports=5, now=lambda: NOW, store=store
            )
        )
    return results


@pytest.fixture
async def sentinel_api(database_url: str, runtime_database_url: str, engine: AsyncEngine) -> Any:
    settings = make_settings(
        database_url,
        web_origin=WEB_ORIGIN,
        trust_proxy=True,
        quota_events_per_day=4,
        auth_fail_per_address=30,
        auth_fail_per_credential=3,
        auth_fail_global=50,
        session_fail_per_address=20,
        session_fail_global=40,
        limits_block_divisor=1000,
    )
    async for api in build_api(database_url, engine, settings=settings):

        @api.app.get("/sentinel-boom/{x}")
        async def boom(x: str) -> None:
            raise RuntimeError(EXC_MESSAGE)

        yield api


async def test_no_log_record_carries_a_sentinel_secret_address_or_dsn_password(
    sentinel_api: Api, database_url: str, tmp_path: Path
) -> None:
    api = sentinel_api
    with capture_everything() as captured:
        secrets, codes = await hostile_http(api)
        await failing_jobs(api)
        await alert_failures(api)
        ticks = await retention_passes(api, tmp_path)
    secrets |= SENTINELS | ADDRESSES | KNOWN_SECRETS | dsn_secrets(database_url)
    secrets |= {api.tenant.context.workspace_id.hex, str(api.tenant.context.workspace_id)}

    # The traffic really exercised the paths (otherwise a clean result proves nothing).
    loggers = {r.name for r in captured.records}
    for expected in (
        "abb_api.request",
        "abb_api.jobs.worker",
        "abb_api.alerts.delivery",
        "abb_api.auth.throttle",
    ):
        assert expected in loggers, (expected, sorted(loggers))
    assert {"SESSION_THROTTLED"} <= codes, codes
    assert [t.mode for t in ticks] == ["dry_run", "enforce", "enforce"]
    assert sum(t.deleted_runs for t in ticks) == 1  # enforce really deleted
    messages = {r.getMessage() for r in captured.records}
    assert "unhandled error" in messages and "job failed" in messages

    assert find_leaks(captured, secrets) == {}


async def test_backup_restore_and_verify_leave_no_secret_in_logs_or_output(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    from tests.backup.helpers import scratch_database

    passphrase_file = write_passphrase(tmp_path)
    bundle = tmp_path / "bundle"
    with capture_everything() as captured:
        outputs = [
            await run_cli(
                "create",
                "--out",
                str(bundle),
                "--encrypt",
                "--passphrase-file",
                str(passphrase_file),
                environ=world.environ(),
            ),
            await run_cli(
                "verify",
                str(bundle),
                "--passphrase-file",
                str(passphrase_file),
                environ=base_environ(),
            ),
            await run_cli(  # a wrong passphrase must fail without echoing anything
                "verify",
                str(bundle),
                "--passphrase-file",
                str(
                    write_passphrase(tmp_path, "wrong SENTINEL-COMMAND-4b18 passphrase!", name="w")
                ),
                environ=base_environ(),
            ),
        ]
        async with scratch_database() as target:
            outputs.append(
                await run_cli(
                    "restore",
                    str(bundle),
                    "--passphrase-file",
                    str(passphrase_file),
                    environ=base_environ(
                        ABB_RESTORE_DATABASE_URL=target,
                        ABB_ARTIFACT_DIR=str(tmp_path / "restored"),
                    ),
                )
            )
    assert [code for code, _, _ in outputs][:2] == [0, 0], outputs
    for _, out, err in outputs:
        captured.extra_text += [out, err]
    secrets = {PASSPHRASE, "wrong SENTINEL-COMMAND-4b18 passphrase!", *KNOWN_SECRETS}
    secrets |= dsn_secrets(world.source_url)
    assert find_leaks(captured, secrets) == {}


def test_the_detector_sees_a_leak_in_every_part_of_a_record() -> None:
    logger = logging.getLogger("abb_api.canary")
    with capture_everything() as captured:
        logger.warning("in message SENTINEL-A")
        logger.warning("in args %s", "SENTINEL-B")
        logger.warning("in extra", extra={"field": "SENTINEL-C"})
        try:
            raise ValueError("SENTINEL-" + "D")  # built at run time: source lines are code
        except ValueError:
            logger.exception("in exception")
    captured.extra_text.append("SENTINEL-E")
    found = find_leaks(captured, {f"SENTINEL-{c}" for c in "ABCDE"})
    # The exception text is the one place the sink removes: D never reaches an emitted line, but
    # the raw record still holds it, which is why the call-site scan exists as well.
    assert {"SENTINEL-A", "SENTINEL-B", "SENTINEL-C", "SENTINEL-E"} <= set(found)
    emitted = "\n".join(captured.lines)
    assert "SENTINEL-D" not in emitted and "ValueError" in emitted


def test_unpinned_libraries_would_have_leaked_at_debug() -> None:
    """Why `configure_logging` pins sqlalchemy and friends: at DEBUG they log SQL parameters."""
    import sqlalchemy

    def run_statement() -> None:
        # The engine decides at creation whether to log statements, from its logger's level.
        engine = sqlalchemy.create_engine("sqlite://")
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT :v"), {"v": "SENTINEL-SQL-PARAM"})

    with capture_everything() as pinned:
        run_statement()
    assert find_leaks(pinned, {"SENTINEL-SQL-PARAM"}) == {}

    with capture_everything() as unpinned:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.DEBUG)
        run_statement()
    assert find_leaks(unpinned, {"SENTINEL-SQL-PARAM"})
