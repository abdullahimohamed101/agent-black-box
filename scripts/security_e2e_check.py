"""API-side half of scripts/security-e2e.sh (Phase 13). Run with `uv run --project apps/api python`.

Subcommands, in the order the script calls them (each prints one JSON evidence line and exits 1 on a failed assertion):

    gate1-verify      the SDK-planted run yields exactly the six findings, linked to events, no content, identical per role
    gate4-benign      an ordinary SDK coding run yields no security finding
    gate2-backstop    an SDK that skips redaction (raw HTTP) is redacted by the server; budgets fail closed
    gate3-modes       FULL / METADATA_ONLY / DISABLED store what they should; metadata-only detectors still report
    gate6-rebuild     deleted findings come back identically from the CLI; settings changes re-analyse without a rebuild
    scan              no secret sentinel anywhere: every text/jsonb column, the artifact directory, the logs

Environment (set by the script): E2E_API, E2E_STRICT_API, E2E_WORKSPACE, E2E_ORIGIN, E2E_STATE (a JSON file the script and this
file share), E2E_OWNER_SESSION / _DEVELOPER_ / _VIEWER_ / _BILLING_, E2E_READ_KEY, E2E_KEY_FULL / _META / _OFF / _STRICT, the
sentinels (E2E_PLANT, E2E_AWS, E2E_AWS2, E2E_PEM_BODY, E2E_PEM_BODY2, E2E_PASSWORD), E2E_ARTIFACT_DIR, E2E_LOGS and
ABB_DATABASE_URL. Secrets are never printed; failures name the check and the endpoint, not the value.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
from abb_event_schema.ids import IdKind, new_id

from abb_api.findings.describe import describe

API = os.environ.get("E2E_API", "http://localhost:8190")
STRICT_API = os.environ.get("E2E_STRICT_API", "http://localhost:8192")
SLUG = os.environ.get("E2E_WORKSPACE", "")
ORIGIN = os.environ.get("E2E_ORIGIN", "http://localhost:3190")
STATE_FILE = Path(os.environ.get("E2E_STATE", "/dev/null"))

SIX = {
    "sec.secret_file_read": "high",
    "sec.credential_in_output": "critical",
    "sec.destructive_command": "critical",
    "sec.risky_git": "high",
    "sec.privileged_db": "high",
    "sec.unexpected_destination": "medium",
}
ROLE_SESSIONS = {
    "owner": "E2E_OWNER_SESSION",
    "developer": "E2E_DEVELOPER_SESSION",
    "viewer": "E2E_VIEWER_SESSION",
    "billing": "E2E_BILLING_SESSION",
}


def fail(message: str) -> None:
    print(f"SECURITY-E2E CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def state() -> dict[str, Any]:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.stat().st_size else {}


def save(**values: Any) -> None:
    current = state()
    current.update(values)
    STATE_FILE.write_text(json.dumps(current))


def emit(**evidence: Any) -> None:
    print(json.dumps(evidence, sort_keys=True))


# ----------------------------------------------------------------------------------------------- clients


_clients: dict[str, httpx.Client] = {}


def person(role: str) -> httpx.Client:
    """A signed-in person (a development session) with the workspace chosen."""
    if role in _clients:
        return _clients[role]
    client = httpx.Client(
        base_url=API,
        cookies={"abb_session": os.environ[ROLE_SESSIONS[role]]},
        headers={"Origin": ORIGIN},
        timeout=60,
    )
    me = client.get("/v1/me")
    me.raise_for_status()
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == SLUG:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            _clients[role] = client
            return client
    fail(f"{role} is not a member of {SLUG}")
    raise AssertionError


def key_client(env: str, base: str = API) -> httpx.Client:
    return httpx.Client(
        base_url=base, headers={"Authorization": f"Bearer {os.environ[env]}"}, timeout=60
    )


def get(role: str, path: str, **params: Any) -> httpx.Response:
    clean = {k: v for k, v in params.items() if v is not None}
    return person(role).get(path, params=clean)


def get_ok(role: str, path: str, **params: Any) -> Any:
    response = get(role, path, **params)
    expect(response.status_code == 200, f"{role} GET {path} -> {response.status_code}")
    return response.json()


# ------------------------------------------------------------------------------------------------ waiting


def wait_until(what: str, probe: Any, timeout: float = 90.0) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(0.5)
    fail(f"timed out waiting for {what}")


def run_current(run_id: str) -> bool:
    run = get_ok("owner", f"/v1/runs/{run_id}")
    findings = get_ok("owner", f"/v1/runs/{run_id}/findings")
    return run.get("summary_state") == "current" and findings["state"]["status"] == "current"


def wait_current(run_id: str) -> None:
    wait_until(f"{run_id} summary and findings", lambda: run_current(run_id))


def security_items(run_id: str, role: str = "owner") -> list[dict[str, Any]]:
    body = get_ok(role, f"/v1/runs/{run_id}/findings")
    return [i for i in body["items"] if i["category"] == "security"]


def detector_set(run_id: str) -> set[str]:
    return {i["detector"] for i in security_items(run_id)}


# --------------------------------------------------------------------------------------------- sentinels


def sentinels() -> dict[str, str]:
    return {
        "plant": os.environ["E2E_PLANT"],
        "aws": os.environ["E2E_AWS"],
        "aws2": os.environ["E2E_AWS2"],
        "pem_body": os.environ["E2E_PEM_BODY"],
        "pem_body2": os.environ["E2E_PEM_BODY2"],
        "password": os.environ["E2E_PASSWORD"],
    }


def leaks_in(text: str, names: tuple[str, ...]) -> list[str]:
    values = sentinels()
    extras = {
        "host": f"exfil-{values['plant']}",
        "deploy_dir": f"deploy-{values['plant']}",
        "notes_dir": f"notes-{values['plant']}",
    }
    pool = {**values, **extras}
    return [n for n in names if (pool.get(n) or n) in text]


FINDING_LEAK_NAMES = ("plant", "aws", "pem_body", "host", "deploy_dir", "notes_dir")
# Pieces of the planted commands and paths. (The fixed rule sentences may name an option, "git push used a force option", so
# the command words themselves are not on this list: only what the planted commands add to them.)
FINDING_LEAK_LITERALS = ("origin main", "users_", "exfil", "collect?", "deploy.txt", "rm -rf /", "DROP TABLE users")


def assert_no_content(body: str, where: str, *, literals: bool = True) -> None:
    for name in leaks_in(body, FINDING_LEAK_NAMES):
        fail(f"{where} contains the planted {name}")
    if literals:
        for literal in FINDING_LEAK_LITERALS:
            if literal in body:
                fail(f"{where} contains {literal!r}")


# ---------------------------------------------------------------------------------------------------- gate 1


def gate1() -> None:
    run_id = state()["planted_run"]
    wait_current(run_id)
    items = security_items(run_id)
    found = {i["detector"]: i for i in items}
    expect(len(items) == 6 and set(found) == set(SIX), f"expected exactly the six detectors, got {sorted(found)}")
    for detector, severity in SIX.items():
        item = found[detector]
        expect(item["severity"] == severity, f"{detector} severity {item['severity']} != {severity}")
        expect(item["kind"] == "probable" and item["evidence"]["count"] >= 1, f"{detector} count")
        expect(item["evidence_event_ids"], f"{detector} cites no event")
        title, message = describe(
            SimpleNamespace(finding_type=item["finding_type"], kind=item["kind"], evidence=item["evidence"])
        )
        expect((item["title"], item["message"]) == (title, message), f"{detector} wording is not the fixed text")
    # every cited event resolves, and is the kind of event the rule is about (read as the OWNER)
    cited = 0
    expected_types = {
        "sec.secret_file_read": {"file.read"},
        "sec.credential_in_output": {"shell.command.started", "shell.command.completed"},
        "sec.destructive_command": {"shell.command.started", "shell.command.completed"},
        "sec.risky_git": {"shell.command.started", "shell.command.completed"},
        "sec.privileged_db": {"shell.command.started", "shell.command.completed"},
        "sec.unexpected_destination": {"shell.command.started", "shell.command.completed"},
    }
    for detector, item in found.items():
        for event_id in item["evidence_event_ids"]:
            event = get_ok("owner", f"/v1/runs/{run_id}/events/{event_id}")
            expect(event["event_type"] in expected_types[detector], f"{detector} cites a {event['event_type']}")
            cited += 1
    # no content in any finding body, for anyone; identical bytes for every role that reads runs
    listing = "/v1/security/findings"
    reads = {
        "run_findings": f"/v1/runs/{run_id}/findings",
        "workspace_list": listing,
        "detectors": "/v1/security/detectors",
    }
    bodies: dict[str, dict[str, bytes]] = {}
    for role in ("owner", "developer", "viewer"):
        bodies[role] = {}
        for name, path in reads.items():
            response = get(role, path, limit=100) if name == "workspace_list" else get(role, path)
            expect(response.status_code == 200, f"{role} {name} -> {response.status_code}")
            bodies[role][name] = response.content
            assert_no_content(response.text, f"{role} {name}")
    reader = key_client("E2E_READ_KEY")
    for name in ("run_findings", "workspace_list"):
        key_body = reader.get(reads[name], params={"limit": 100} if name == "workspace_list" else {})
        expect(key_body.status_code == 200, f"read key {name} -> {key_body.status_code}")
        assert_no_content(key_body.text, f"read key {name}")
        for role in ("developer", "viewer"):
            expect(bodies[role][name] == bodies["owner"][name], f"{role} {name} differs from the owner's bytes")
        expect(key_body.content == bodies["owner"][name], f"read key {name} differs from the owner's bytes")
    for path in (reads["run_findings"], listing):
        expect(get("billing", path).status_code == 403, f"billing {path} is not 403")
    # the workspace list agrees with the run's findings, and its facets name only fixed words
    listed = [i for i in json.loads(bodies["viewer"]["workspace_list"])["items"] if i["run_id"] == run_id]
    expect({i["detector"] for i in listed} == set(SIX), "the workspace list lacks one of the six")
    facets = json.loads(bodies["viewer"]["workspace_list"])["facets"]
    assert_no_content(json.dumps(facets), "facets")
    # a viewer cannot learn the planted path or host from the run, its events, its spans or search
    viewer_text = ""
    for path in (f"/v1/runs/{run_id}", f"/v1/runs/{run_id}/events?limit=500", f"/v1/runs/{run_id}/spans?limit=500"):
        response = person("viewer").get(path)
        expect(response.status_code == 200, f"viewer {path} -> {response.status_code}")
        viewer_text += response.text
    assert_no_content(viewer_text, "viewer run/events/spans", literals=False)
    for term in (sentinels()["plant"], f"exfil-{sentinels()['plant']}", "DROP TABLE", "id_rsa"):
        search = get_ok("viewer", "/v1/runs", q=term, limit=50)
        expect(run_id not in {r["id"] for r in search["items"]}, "a viewer's search matched the planted run on content")
    # the SDK redacted all three channels; the stored artifacts carry markers, never the values
    events = get_ok("owner", f"/v1/runs/{run_id}/events", limit=500)["items"]
    artifacts = [
        v.removeprefix("artifact://")
        for e in events
        for k, v in e["attributes"].items()
        if k in ("shell.stdout_artifact", "diff.artifact") and isinstance(v, str)
    ]
    bodies_text = ""
    for artifact_id in artifacts:
        response = get("owner", f"/v1/artifacts/{artifact_id}/content")
        expect(response.status_code == 200, f"artifact content -> {response.status_code}")
        bodies_text += response.text
    expect(bool(artifacts) and "[REDACTED:" in bodies_text, "no redaction marker in the stored artifacts")
    for name in ("aws", "pem_body"):
        expect(not leaks_in(bodies_text, (name,)), f"stored artifacts contain the planted {name}")
    emit(
        gate=1,
        run=run_id,
        findings=sorted(found),
        severities={d: found[d]["severity"] for d in sorted(found)},
        evidence_events_resolved=cited,
        roles_byte_identical=["owner", "developer", "viewer", "read key"],
        billing_status=403,
        artifacts_checked=len(artifacts),
    )


def gate4() -> None:
    run_id = state()["benign_run"]
    wait_current(run_id)
    items = security_items(run_id)
    expect(items == [], f"the ordinary run has security findings: {[i['detector'] for i in items]}")
    run = get_ok("owner", f"/v1/runs/{run_id}")
    expect(run["security_findings"]["count"] == 0, "the run chip would show findings")
    events = get_ok("owner", f"/v1/runs/{run_id}/events", limit=500)["items"]
    shell = [e for e in events if e["event_type"] == "shell.command.completed"]
    emit(gate=4, run=run_id, events=len(events), shell_commands=len(shell), security_findings=0)


# ---------------------------------------------------------------------------------------------- database


def db_url() -> str:
    return os.environ["ABB_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def scan_database(needles: list[str]) -> tuple[dict[str, list[str]], int, int]:
    """Where each needle occurs, in every text-like column of the database. -> (hits, columns, rows)"""
    conn = await asyncpg.connect(db_url())
    try:
        columns = await conn.fetch(
            "select table_name, column_name, data_type from information_schema.columns "
            "where table_schema = 'public' and data_type in "
            "('text', 'character varying', 'jsonb', 'json', 'tsvector', 'ARRAY', 'bytea') "
            "order by table_name, ordinal_position"
        )
        patterns = [f"%{n}%" for n in needles]
        hits: dict[str, list[str]] = {n: [] for n in needles}
        rows = 0
        seen_tables: set[str] = set()
        for column in columns:
            table, name, kind = column["table_name"], column["column_name"], column["data_type"]
            expr = f'encode("{name}", \'escape\')' if kind == "bytea" else f'"{name}"::text'
            found = await conn.fetch(
                f'select {expr} as v from "{table}" where {expr} ilike any($1::text[])',  # noqa: S608
                patterns,
            )
            if table not in seen_tables:
                seen_tables.add(table)
                rows += await conn.fetchval(f'select count(*) from "{table}"')  # noqa: S608
            for row in found:
                for needle in needles:
                    if needle.lower() in row["v"].lower():
                        hits[needle].append(f"{table}.{name}")
        return hits, len(columns), rows
    finally:
        await conn.close()


def scan_files(needles: list[str], root: Path) -> tuple[dict[str, list[str]], int, int]:
    hits: dict[str, list[str]] = {n: [] for n in needles}
    files = [p for p in root.rglob("*") if p.is_file()]
    total = 0
    for path in files:
        data = path.read_bytes()
        total += len(data)
        for needle in needles:
            if needle.encode() in data:
                hits[needle].append(path.name[:16])
    return hits, len(files), total


def scan(*, label: str) -> None:
    values = sentinels()
    secrets = [values["aws"], values["aws2"], values["pem_body"], values["pem_body2"], values["password"]]
    pem_lines = ["BEGIN RSA PRIVATE KEY"]
    hits, columns, rows = asyncio.run(scan_database(secrets + pem_lines))
    # The plant tag is metadata (a path, a command) stored in FULL mode: the scan must be able to find a sentinel at all.
    control, _, _ = asyncio.run(scan_database([values["plant"]]))
    expect(any(c.startswith("events.") for c in control[values["plant"]]), "positive control: the scan did not find the plant tag in events")
    for needle, places in hits.items():
        label_needle = "a PEM header" if needle in pem_lines else "a planted secret"
        expect(not places, f"{label_needle} is stored in {sorted(set(places))}")
    file_hits, file_count, file_bytes = scan_files(secrets + pem_lines, Path(os.environ["E2E_ARTIFACT_DIR"]))
    for needle, places in file_hits.items():
        expect(not places, f"a planted secret is on disk in {places}")
    log_hits = 0
    for log in filter(None, os.environ.get("E2E_LOGS", "").split(":")):
        data = Path(log).read_bytes() if Path(log).exists() else b""
        log_hits += sum(n.encode() in data for n in secrets)
    expect(log_hits == 0, "a planted secret is in a server log")
    emit(
        scan=label,
        sentinels=len(secrets) + len(pem_lines),
        columns_scanned=columns,
        rows_in_tables=rows,
        artifact_files=file_count,
        artifact_bytes=file_bytes,
        found=0,
        positive_control_columns=sorted(set(control[values["plant"]]))[:4],
    )


async def fetch_events(run_id: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(db_url())
    try:
        return list(
            await conn.fetch(
                "select event_type, attributes, payload, tags from events where run_id = $1 order by sequence",
                _uuid(run_id),
            )
        )
    finally:
        await conn.close()


def _uuid(public_id: str) -> uuid.UUID:
    from abb_event_schema.ids import to_uuid

    return to_uuid(public_id)


async def fetch_artifacts(run_id: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(db_url())
    try:
        return list(
            await conn.fetch(
                "select id, artifact_type, name, size_bytes, metadata from artifacts where run_id = $1 order by created_at",
                _uuid(run_id),
            )
        )
    finally:
        await conn.close()


def jsonb(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------------------------- raw HTTP


def pem(body: str) -> str:
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"


class RawRun:
    """A run written by hand over HTTP: an 'SDK' that does no redaction at all."""

    def __init__(self, key_env: str, base: str = API, *, agent: str = "raw-agent") -> None:
        self.client = key_client(key_env, base)
        self.base = base
        self.run_id = new_id(IdKind.RUN)
        self.trace_id = new_id(IdKind.TRACE)
        self.agent = agent
        self.events: list[dict[str, Any]] = []

    def add(self, event_type: str, attributes: dict[str, Any] | None = None, **fields: Any) -> str:
        n = len(self.events) + 1
        event_id = new_id(IdKind.EVENT)
        event = {
            "schema_version": "1.0",
            "event_id": event_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "span_id": new_id(IdKind.SPAN),
            "agent_id": self.agent,
            "event_type": event_type,
            "sequence": n,
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 120 + n)) + ".000Z",
            "attributes": attributes or {},
        }
        event.update(fields)
        self.events.append(event)
        return event_id

    def send(self) -> dict[str, Any]:
        response = self.client.post("/v1/events/batch", json={"events": self.events})
        expect(response.status_code in (200, 202), f"raw batch -> {response.status_code} {response.text[:200]}")
        body = response.json()
        expect(body["accepted"] == len(self.events) and body["rejected"] == 0, f"raw batch not accepted: {body}")
        return body

    def artifact(self, body: bytes, *, kind: str = "stdout", name: str | None = None) -> tuple[str, int]:
        artifact_id = new_id(IdKind.ARTIFACT)
        params = {"run_id": self.run_id, "kind": kind}
        if name:
            params["name"] = name
        response = self.client.put(
            f"/v1/artifacts/{artifact_id}", params=params, content=body, headers={"content-type": "text/plain"}
        )
        return artifact_id, response.status_code


def settle(run_id: str) -> None:
    wait_current(run_id)


def gate2() -> None:
    values = sentinels()
    aws, body = values["aws2"], values["pem_body2"]
    # --- the main API: a normal budget
    raw = RawRun("E2E_KEY_FULL")
    raw.add("run.started", {"run.name": "Security E2E: raw HTTP run"})
    artifact_id, status = raw.artifact(
        f"deploy log\nAWS_ACCESS_KEY_ID={aws}\n{pem(body)}\ndone\n".encode(), name=f"deploy-{aws}.log"
    )
    expect(status == 201, f"artifact upload -> {status}")
    raw.add(
        "shell.command.completed",
        {"shell.command": f"deploy --key {aws}", "shell.exit_code": 0, "shell.stdout_artifact": f"artifact://{artifact_id}"},
        payload={"stdout": f"AWS_ACCESS_KEY_ID={aws}\n{pem(body)}", "password": values["password"], "note": "ok"},
    )
    raw.add(
        "tool.call.failed",
        {"tool.name": "deployer", "error.message": f"401 for key {aws}"},
        tags=["raw"],
    )
    raw.add(  # more nodes than the scan budget allows: the payload is dropped, with the secret in it
        "tool.call.completed",
        {"tool.name": "bulk"},
        payload={"items": ["x"] * 5100, "secret_tail": f"{aws} {pem(body)}"},
    )
    raw.add("run.completed", status="success")
    raw.send()
    settle(raw.run_id)
    rows = asyncio.run(fetch_events(raw.run_id))
    by_type = {r["event_type"]: r for r in rows}
    shell = by_type["shell.command.completed"]
    attrs = jsonb(shell["attributes"])
    expect(attrs.get("redaction.server_count", 0) >= 3, "the shell event records no server redaction")
    kinds = set(attrs.get("redaction.server_kinds", []))
    expect({"aws_access_key", "private_key"} <= kinds, f"server kinds missing: {sorted(kinds)}")
    payload = jsonb(shell["payload"])
    expect(payload is not None and "[REDACTED:" in json.dumps(payload), "the payload was not redacted in place")
    expect(payload.get("password") == "[REDACTED:password]", "the denied key was not replaced")
    failed_attrs = jsonb(by_type["tool.call.failed"]["attributes"])
    expect("[REDACTED:aws_access_key]" in failed_attrs["error.message"], "the error message was not redacted")
    big_row = [r for r in rows if r["event_type"] == "tool.call.completed"][0]
    big_attrs = jsonb(big_row["attributes"])
    expect(big_row["payload"] is None and big_attrs.get("redaction.dropped_reason") == "too_large", "an oversized payload was not dropped as too_large")
    artifacts = asyncio.run(fetch_artifacts(raw.run_id))
    expect(len(artifacts) == 1, "the artifact row is missing")
    record = jsonb(artifacts[0]["metadata"]).get("redaction", {})
    expect(record.get("server_count", 0) >= 2 and "aws_access_key" in record.get("server_kinds", []), f"artifact redaction record: {record}")
    content = get("owner", f"/v1/artifacts/{artifact_id}/content")
    expect(content.status_code == 200 and "[REDACTED:aws_access_key]" in content.text and aws not in content.text, "stored artifact body not redacted")
    # the credential finding of the raw run cites events and the artifact, never the value
    wait_until("the raw run's credential finding", lambda: "sec.credential_in_output" in detector_set(raw.run_id))
    credential = [i for i in security_items(raw.run_id) if i["detector"] == "sec.credential_in_output"][0]
    expect(credential["evidence"].get("artifact_ids") == [artifact_id], "the finding does not cite the redacted artifact")
    expect({"aws_access_key", "private_key"} <= set(credential["evidence"].get("kinds", [])), "finding kinds")
    assert_no_content(json.dumps(credential), "raw credential finding", literals=False)
    expect(not leaks_in(json.dumps(credential), ("aws2", "pem_body2")), "finding contains the secret")
    # --- the strict API: a budget so small that every scan fails, and an artifact cap of 2 KiB
    strict = RawRun("E2E_KEY_STRICT", STRICT_API)
    strict.add("run.started", {"run.name": "Security E2E: scan budget run"})
    strict.add(
        "shell.command.completed",
        {"shell.command": f"echo {aws}", "shell.exit_code": 0},
        payload={"stdout": f"{aws}\n{pem(body)}"},
    )
    strict.add("run.completed", status="success")
    big_artifact, status_big = strict.artifact((f"{aws}\n{pem(body)}\n" + "padding line\n" * 400).encode())
    small_artifact, status_small = strict.artifact(f"tiny {aws}\n".encode())
    expect(status_big == 201 and status_small == 201, f"strict artifact uploads -> {status_big}, {status_small}")
    strict.send()
    srows = asyncio.run(fetch_events(strict.run_id))
    shell_row = [r for r in srows if r["event_type"] == "shell.command.completed"][0]
    sattrs = jsonb(shell_row["attributes"])
    expect(shell_row["payload"] is None, "a payload that could not be scanned was stored")
    expect(sattrs.get("redaction.dropped_reason") == "scan_failed", f"scan failure reason: {sattrs.get('redaction.dropped_reason')}")
    expect(aws not in json.dumps(sattrs), "an attribute that could not be scanned was stored")
    sarts = {str(a["id"]): jsonb(a["metadata"]).get("redaction", {}) for a in asyncio.run(fetch_artifacts(strict.run_id))}
    reasons = sorted(r.get("dropped_reason") for r in sarts.values())
    expect(reasons == ["scan_failed", "too_large"], f"artifact drop reasons: {reasons}")
    for artifact in (big_artifact, small_artifact):
        text = get("owner", f"/v1/artifacts/{artifact}/content")
        expect(text.status_code == 200 and "not stored" in text.text and aws not in text.text, "a dropped artifact leaked or is not a marker")
    scan(label="after gate 2")
    emit(
        gate=2,
        raw_run=raw.run_id,
        server_count=attrs["redaction.server_count"],
        server_kinds=sorted(kinds),
        payload_redacted=True,
        oversized_payload_dropped=big_attrs["redaction.dropped_reason"],
        artifact_server_count=record["server_count"],
        strict_event_reason=sattrs["redaction.dropped_reason"],
        strict_artifact_reasons=reasons,
        credential_finding_kinds=sorted(credential["evidence"]["kinds"]),
    )


# ---------------------------------------------------------------------------------------------------- gate 3


def gate3() -> None:
    values = sentinels()
    aws = values["aws2"]
    tag = f"MODE{uuid.uuid4().hex[:10].upper()}"
    out: dict[str, Any] = {}
    runs: dict[str, RawRun] = {}
    artifact_ids: dict[str, str] = {}
    for mode, env in (("FULL", "E2E_KEY_FULL"), ("METADATA_ONLY", "E2E_KEY_META"), ("DISABLED", "E2E_KEY_OFF")):
        raw = RawRun(env)
        raw.add("run.started", {"run.name": f"Security E2E: {mode}", "metadata.owner": f"FREE-{tag}"})
        artifact_id, status = raw.artifact(f"stdout {aws}\nBODYTEXT-{tag}\n".encode())
        expect(status == 201, f"{mode} artifact -> {status}")
        raw.add(
            "shell.command.completed",
            {"shell.command": "rm -rf / # modes", "shell.risk_class": "R4", "shell.exit_code": 1,
             "shell.stdout_artifact": f"artifact://{artifact_id}"},
            payload={"stdout": f"AWS={aws}", "note": f"PAYLOADTEXT-{tag}"},
        )
        raw.add("tool.call.failed", {"tool.name": "t", "error.message": f"FREETEXT-{tag} boom"}, payload={"detail": f"DETAIL-{tag}"})
        raw.add("shell.command.completed", {"shell.command": "git push --force origin main", "shell.risk_class": "R3", "shell.exit_code": 0})
        raw.add("run.completed", status="success")
        raw.send()
        runs[mode], artifact_ids[mode] = raw, artifact_id
    for mode, raw in runs.items():
        settle(raw.run_id)
        rows = asyncio.run(fetch_events(raw.run_id))
        text = json.dumps([[r["event_type"], jsonb(r["attributes"]), jsonb(r["payload"]), r["tags"]] for r in rows])
        expect(aws not in text, f"{mode}: the AWS key is stored in an event")
        payloads = [r for r in rows if r["payload"] is not None]
        shell = [jsonb(r["attributes"]) for r in rows if r["event_type"] == "shell.command.completed"][0]
        art = asyncio.run(fetch_artifacts(raw.run_id))
        expect(len(art) == 1, f"{mode}: artifact row")
        record = jsonb(art[0]["metadata"]).get("redaction", {})
        body = get("owner", f"/v1/artifacts/{artifact_ids[mode]}/content").text
        expect(aws not in body, f"{mode}: the artifact body holds the key")
        api_text = json.dumps(get_ok("owner", f"/v1/runs/{raw.run_id}/events", limit=100))
        found = detector_set(raw.run_id)
        if mode == "FULL":
            expect(len(payloads) == 2 and f"PAYLOADTEXT-{tag}" in text and "[REDACTED:aws_access_key]" in text, "FULL: payloads not stored redacted")
            expect(f"FREETEXT-{tag}" in text and f"FREE-{tag}" in text, "FULL: free text missing")
            expect(f"BODYTEXT-{tag}" in body and "[REDACTED:aws_access_key]" in body and "dropped_reason" not in record, "FULL: artifact body")
            expect("shell.stdout_artifact" in shell, "FULL: artifact reference dropped")
        else:
            reason = "mode_metadata_only" if mode == "METADATA_ONLY" else "mode_disabled"
            expect(payloads == [], f"{mode}: a payload is stored")
            expect(f"PAYLOADTEXT-{tag}" not in text and f"DETAIL-{tag}" not in text, f"{mode}: payload text is stored")
            expect("shell.stdout_artifact" not in shell and shell.get("redaction.dropped_reason") == reason, f"{mode}: artifact reference / reason: {shell.get('redaction.dropped_reason')}")
            expect(f"BODYTEXT-{tag}" not in body and "not stored" in body and record.get("dropped_reason") == reason, f"{mode}: artifact body stored")
            expect(f"BODYTEXT-{tag}" not in api_text and f"PAYLOADTEXT-{tag}" not in api_text, f"{mode}: the API returns content")
            # Dropped before it is read: the server neither stores nor scans what a mode does not keep.
            expect("redaction.server_count" not in shell, f"{mode}: a dropped payload was scanned")
            free = f"FREETEXT-{tag}" in text, f"FREE-{tag}" in text
            if mode == "METADATA_ONLY":
                expect(all(free), "METADATA_ONLY: free text should be kept")
            else:
                expect(not any(free), "DISABLED: free text is stored")
        # Metadata is enough for the command detectors in every mode; the credential finding needs the server to have seen a secret,
        # which only FULL mode scans for (the other modes drop the payload and the artifact body unread).
        wanted = {"sec.destructive_command", "sec.risky_git"} | ({"sec.credential_in_output"} if mode == "FULL" else set())
        expect(found == wanted, f"{mode}: detectors {sorted(found)}")
        out[mode] = {
            "run": raw.run_id,
            "payloads_stored": len(payloads),
            "artifact_body": "redacted copy" if mode == "FULL" else f"placeholder ({record.get('dropped_reason')})",
            "free_text_stored": f"FREETEXT-{tag}" in text,
            "findings": sorted(d.removeprefix("sec.") for d in found),
        }
    emit(gate=3, modes=out)


# ---------------------------------------------------------------------------------------------------- gate 6


def finding_view(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("id", "detector", "detector_version", "severity", "kind", "title", "message", "evidence", "evidence_event_ids", "start_event_id", "end_event_id")
    return sorted(({k: i[k] for k in keep if k in i} for i in items), key=lambda i: i["id"])


def put_settings(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    response = person("owner").put(f"/v1/projects/{project_id}/security-settings", json=body)
    expect(response.status_code == 200, f"PUT settings -> {response.status_code} {response.text[:200]}")
    return response.json()


def cli(*args: str) -> str:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "abb_api.cli", *args], capture_output=True, text=True, check=False, env=os.environ
    )
    expect(done.returncode == 0, f"cli {args[0]} failed: {done.stderr.strip()[-200:]}")
    return done.stdout + done.stderr


async def delete_findings(run_id: str) -> tuple[int, int]:
    conn = await asyncpg.connect(db_url())
    try:
        a = await conn.execute("delete from findings where run_id = $1", _uuid(run_id))
        b = await conn.execute("delete from run_findings where run_id = $1", _uuid(run_id))
        return int(a.split()[-1]), int(b.split()[-1])
    finally:
        await conn.close()


def gate6() -> None:
    st = state()
    run_id, project = st["planted_run"], st["project_full"]
    before = finding_view(security_items(run_id))
    expect(len(before) == 6, "precondition: six findings")
    removed = asyncio.run(delete_findings(run_id))
    expect(security_items(run_id) == [], "findings are still readable after the delete")
    t0 = time.monotonic()
    cli("rebuild-findings", "--workspace", SLUG, "--project", "full")
    after = wait_until("the rebuilt findings", lambda: finding_view(security_items(run_id)) if len(security_items(run_id)) == 6 else None)
    expect(after == before, "the rebuilt findings differ from the deleted ones (ids, evidence, wording)")
    rebuild_seconds = round(time.monotonic() - t0, 1)
    # settings change -> background re-analysis, no manual rebuild
    original = get_ok("owner", f"/v1/projects/{project}/security-settings")
    allowlist = original["destination_allowlist"]
    timings: dict[str, float] = {}

    def until(label: str, wanted: set[str]) -> None:
        t = time.monotonic()
        wait_until(label, lambda: detector_set(run_id) == wanted, timeout=60)
        timings[label] = round(time.monotonic() - t, 1)

    full = set(SIX)
    put_settings(project, {"destination_allowlist": [*allowlist, "*.example.net"]})
    until("allowlist change removes unexpected_destination", full - {"sec.unexpected_destination"})
    put_settings(project, {"muted_detectors": ["sec.risky_git"]})
    until("mute removes risky_git", full - {"sec.unexpected_destination", "sec.risky_git"})
    put_settings(project, {"muted_detectors": [], "destination_allowlist": allowlist})
    until("unmute and allowlist restore the six", full)
    restored = finding_view(security_items(run_id))
    expect(restored == before, "after unmuting the findings are not the original ones (same ids expected)")
    # a run of another project is untouched, and the ordinary run still has nothing
    expect(security_items(st["benign_run"]) == [], "the ordinary run gained a finding")
    audit = get_ok("owner", "/v1/audit", limit=100)["items"]
    changes = [a for a in audit if a["action"] == "security.settings.update" and a["resource_id"] == project]
    expect(len(changes) >= 4, f"audit rows for settings changes: {len(changes)}")
    expect(get("developer", f"/v1/projects/{project}/security-settings").status_code == 403, "developer can read settings")
    put_denied = person("viewer").put(f"/v1/projects/{project}/security-settings", json={"muted_detectors": []})
    expect(put_denied.status_code == 403, f"viewer PUT -> {put_denied.status_code}")
    emit(
        gate=6,
        run=run_id,
        deleted_rows={"findings": removed[0], "run_findings": removed[1]},
        rebuilt_identical=True,
        rebuild_seconds=rebuild_seconds,
        background_reanalysis_seconds=timings,
        audit_rows=len(changes),
    )


def put_allowlist() -> None:
    projects = get_ok("owner", "/v1/projects")["items"]
    project = next(p["id"] for p in projects if p["slug"] == "full")
    save(project_full=project)
    stored = put_settings(project, {"destination_allowlist": ["api.example.com"]})
    emit(allowlist=stored["destination_allowlist"], capture_mode=stored["capture_mode"])


def main() -> None:
    commands = {
        "gate1-verify": gate1,
        "gate4-benign": gate4,
        "gate2-backstop": gate2,
        "gate3-modes": gate3,
        "gate6-rebuild": gate6,
        "scan": lambda: scan(label=sys.argv[2] if len(sys.argv) > 2 else "final"),
        "put-allowlist": put_allowlist,
        "save": lambda: save(**{sys.argv[2]: sys.argv[3]}),
    }
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        fail(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}}")
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
