"""The security detectors through the `detect_findings` job: artifacts, project settings, project
isolation, INV-2 (a rebuild equals a first run) and the cost of a large run."""

import json
import time
from typing import Any

from abb_event_schema.ids import IdKind, new_id, to_uuid
from sqlalchemy import select

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.db import tables as t
from abb_api.findings.repository import FindingsRepository
from abb_api.security.settings import PgSecuritySettingsStore
from tests.api_fixtures import Api
from tests.security_helpers import AWS_KEY, PLANT, Run, planted_run


async def stored(api: Api, run_id: str, tenant: Any = None) -> list[Any]:
    tenant = tenant or api.tenant
    async with api.engine.connect() as conn:
        _, rows = await FindingsRepository(conn, tenant.context).for_run(to_uuid(run_id))
    return [r for r in rows if r.detector.startswith("sec.")]


async def upload(api: Api, run_id: str, body: str) -> str:
    async with api.engine.begin() as conn:
        key = await ApiKeyRepository(conn, api.tenant.context).create(
            scopes=frozenset({scopes.ARTIFACTS_WRITE}), project_id=api.tenant.project_uuids["alpha"]
        )
    artifact_id = new_id(IdKind.ARTIFACT)
    response = await api.client.put(
        f"/v1/artifacts/{artifact_id}",
        params={"run_id": run_id, "kind": "stdout"},
        content=body.encode(),
        headers={"authorization": f"Bearer {key.token}", "content-type": "text/plain"},
    )
    assert response.status_code == 201, response.text
    return artifact_id


async def test_an_artifact_the_server_redacted_becomes_a_credential_finding(api: Api) -> None:
    run = Run()
    artifact = await upload(api, run.run_id, f"deploy ok\nAWS_SECRET_KEY={AWS_KEY}\nbye\n")
    clean = await upload(api, run.run_id, "all tests passed\n")
    run.add(
        "shell.command.completed",
        {"shell.command": "make deploy", "shell.stdout_artifact": f"artifact://{artifact}"},
    )
    run.add(
        "shell.command.completed",
        {"shell.command": "make test", "shell.stdout_artifact": f"artifact://{clean}"},
    )
    assert (await api.post_batch(run.wires)).status_code == 202
    await api.drain()
    (finding,) = await stored(api, run.run_id)
    assert finding.detector == "sec.credential_in_output" and finding.severity == "critical"
    assert finding.evidence["kinds"] == ["aws_access_key"]
    assert finding.evidence["artifact_ids"] == [artifact]
    assert clean not in json.dumps(finding.evidence)
    assert len(finding.evidence_event_ids) == 1  # the event that cites the artifact
    assert AWS_KEY not in json.dumps(finding.evidence)


async def test_findings_follow_the_projects_own_settings_only(api: Api) -> None:
    """Alpha has an allowlist and a mute; beta (same workspace) and globex have neither."""
    alpha_run, _ = planted_run("alpha")
    beta_run, _ = planted_run("beta")
    other_run, _ = planted_run("globex")
    async with api.engine.begin() as conn:
        store = PgSecuritySettingsStore(conn, api.tenant.context)
        await store.upsert(
            api.tenant.project_uuids["alpha"],
            updated_by="test",
            muted_detectors=["sec.risky_git"],
            destination_allowlist=["api.example.com"],
        )
    for run, token in ((alpha_run, "writer"), (beta_run, "beta"), (other_run, "other")):
        assert (await api.post_batch(run.wires, token=token)).status_code == 202
    await api.drain()
    alpha = {f.detector for f in await stored(api, alpha_run.run_id)}
    beta = {f.detector for f in await stored(api, beta_run.run_id)}
    globex = {f.detector for f in await stored(api, other_run.run_id, api.other)}
    everything = {
        "sec.secret_file_read",
        "sec.credential_in_output",
        "sec.destructive_command",
        "sec.risky_git",
        "sec.privileged_db",
    }
    assert alpha == (everything - {"sec.risky_git"}) | {"sec.unexpected_destination"}
    assert beta == everything  # no allowlist: no destination findings; nothing muted
    assert globex == everything


async def test_a_second_analysis_of_the_same_events_changes_nothing_but_updated_at(
    api: Api,
) -> None:
    run, _ = planted_run("inv2")
    assert (await api.post_batch(run.wires)).status_code == 202
    await api.drain()
    first = await stored(api, run.run_id)
    async with api.engine.begin() as conn:
        from abb_api.jobs.outbox import OutboxRepository

        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run.run_id)])
    await api.drain()
    second = await stored(api, run.run_id)
    assert [(f.id, f.fingerprint, f.evidence) for f in first] == [
        (f.id, f.fingerprint, f.evidence) for f in second
    ]
    assert PLANT not in json.dumps([f.evidence for f in second])


async def test_a_run_that_stops_matching_loses_its_finding(api: Api) -> None:
    """Findings are derived: when the rule or the settings change, rows that no longer match go."""
    run = Run()
    run.add("http.request", {"http.url": "https://off.example.net/x"})
    async with api.engine.begin() as conn:
        await PgSecuritySettingsStore(conn, api.tenant.context).upsert(
            api.tenant.project_uuids["alpha"],
            updated_by="t",
            destination_allowlist=["a.example.com"],
        )
    assert (await api.post_batch(run.wires)).status_code == 202
    await api.drain()
    assert [f.detector for f in await stored(api, run.run_id)] == ["sec.unexpected_destination"]
    async with api.engine.begin() as conn:
        await PgSecuritySettingsStore(conn, api.tenant.context).upsert(
            api.tenant.project_uuids["alpha"],
            updated_by="t",
            destination_allowlist=["off.example.net"],
        )
        from abb_api.jobs.outbox import OutboxRepository

        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run.run_id)])
    await api.drain()
    assert await stored(api, run.run_id) == []
    async with api.engine.connect() as conn:
        state = (
            await conn.execute(
                select(
                    t.run_findings.c.security_count, t.run_findings.c.security_max_severity
                ).where(t.run_findings.c.run_id == to_uuid(run.run_id))
            )
        ).one()
    assert tuple(state) == (0, None)


async def test_a_run_of_ten_thousand_events_is_analysed_within_a_bound(api: Api) -> None:
    run = Run()
    commands = ["pytest -q", "git status", "ls -la", "npm test", "cat README.md", "make build"]
    while len(run.wires) < 10_000:
        run.shell(f"{commands[len(run.wires) % len(commands)]} --n {len(run.wires)}", "R1")
    run.file("file.read", ".env")
    for start in range(0, len(run.wires), 500):
        assert (await api.post_batch(run.wires[start : start + 500])).status_code == 202
    started = time.perf_counter()
    await api.drain()
    elapsed = time.perf_counter() - started
    rows = await stored(api, run.run_id)
    assert [f.detector for f in rows] == ["sec.secret_file_read"]
    async with api.engine.connect() as conn:
        seen = await conn.scalar(
            select(t.run_findings.c.event_count_seen).where(
                t.run_findings.c.run_id == to_uuid(run.run_id)
            )
        )
    assert seen is not None and seen >= 10_000
    print(f"summarize + detect over {seen} events: {elapsed:.2f} s")
    assert elapsed < 60
