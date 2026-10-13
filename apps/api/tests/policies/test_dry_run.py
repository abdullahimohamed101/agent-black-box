"""The dry run: event-to-intent mapping, counting, bounds, and that no content leaves (ADR-140)."""

import json
import time
import uuid
from typing import Any

import pytest

from abb_api.policies.api_service import PolicyApiService
from abb_api.policies.dryrun import DryRunLimits, evaluate_events
from abb_api.policies.mapping import MAPPED_EVENT_TYPES, executable_of, intent_for
from abb_api.policies.model import Intent
from abb_api.policies.parser import parse_policy
from abb_api.runs.event_queries import ScannedEvent
from tests.api_fixtures import Api, person
from tests.ingest_helpers import build_event, make_run_ids
from tests.test_event_store import ingest

SENTINEL = "SENTINEL-dry-run-5d1e"
TMP_DIR = "/tmp/x"  # noqa: S108


def shell(command: str, risk: str) -> dict[str, Any]:
    return {"shell.command": command, "shell.risk_class": risk}


def path(file: str) -> dict[str, Any]:
    return {"file.path": file}


# ------------------------------------------------------------------------------------- mapping


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git push origin main", "git"),
        ("/usr/bin/git status", "git"),
        ("FOO=1 BAR=2 python -m pytest", "python"),
        ("sudo rm -rf /tmp/x", "rm"),
        ("env NODE_ENV=production node app.js", "node"),
        ("  ls  ", "ls"),
        ("", ""),
        ("A=1", ""),
        ("x" * 500, "x" * 128),
    ],
)
def test_executable_names(command: str, expected: str) -> None:
    assert executable_of(command) == expected


def test_each_event_type_maps_to_the_documented_intent() -> None:
    assert intent_for(
        "shell.command.started",
        {"shell.command": "git push -f", "shell.risk_class": "R3", "shell.category": "network"},
    ) == Intent("shell.execute", "git", "R3", {"category": "network"})
    assert intent_for(
        "shell.command.started", {"shell.command": "ls", "shell.risk_class": "R9"}
    ) == (Intent("shell.execute", "ls", None, {}))
    assert intent_for("file.read", {"file.path": "src/a.py"}) == Intent(
        "filesystem.read", "src/a.py"
    )
    for suffix in ("created", "modified", "deleted"):
        assert intent_for(f"file.{suffix}", {"file.path": "p"}) == Intent(
            "filesystem.write", "p", None, {"operation": suffix}
        )
    assert intent_for("git.push", {"git.push_target": "origin main", "git.branch": "main"}) == (
        Intent("github.push", "origin main", None, {"branch": "main"})
    )
    assert intent_for("tool.call.started", {"tool.name": "github", "tool.operation": "merge"}) == (
        Intent("tool.github", "merge")
    )
    odd = intent_for("tool.call.started", {"tool.name": "my tool/ü"})
    assert odd is not None and odd.capability == "tool.my_tool__"
    assert intent_for("tool.call.started", {"tool.name": ""}) is None
    assert intent_for("tool.call.started", {}) is None
    assert intent_for("llm.request.completed", {"llm.model": "m"}) is None
    assert set(MAPPED_EVENT_TYPES) == {
        "shell.command.started", "file.read", "file.created", "file.modified", "file.deleted",
        "git.push", "tool.call.started",
    }  # fmt: skip


def test_non_text_attribute_values_never_break_the_mapping() -> None:
    odd: dict[str, Any] = {"shell.command": 5, "file.path": ["x"], "git.push_target": None}
    assert intent_for("shell.command.started", odd) == Intent("shell.execute", "", None, {})
    assert intent_for("file.read", odd) == Intent("filesystem.read", "")
    assert intent_for("git.push", odd) == Intent("github.push", "", None, {})


# ----------------------------------------------------------------------------------- evaluation

DRAFT = """\
version: 1
policies:
  - id: deny-env-read
    when: {capability: filesystem.read, resource: "**/.env*"}
    decision: deny
  - id: approve-risky-git
    when: {capability: shell.execute, resource: git, risk_class: [R3, R4]}
    decision: require_approval
  - id: deny-r4
    when: {capability: shell.execute, risk_class: R4}
    decision: deny
  - id: approve-push-main
    when: {capability: github.push, attributes: {branch: main}}
    decision: require_approval
"""


def scanned(event_type: str, **attributes: Any) -> ScannedEvent:
    return ScannedEvent(uuid.uuid4(), uuid.uuid4(), event_type, attributes)


def fixture_events() -> list[ScannedEvent]:
    return [
        scanned("shell.command.started", **shell(f"git push {SENTINEL}", "R3")),
        scanned("shell.command.started", **shell("ls -la", "R0")),
        scanned("file.read", **path(f"{SENTINEL}/.env")),
        scanned("file.read", **path("src/app.py")),
        scanned("file.modified", **path("src/app.py")),
        scanned("tool.call.started", **{"tool.name": "github"}),
        scanned("shell.command.started", **shell(f"rm -rf {TMP_DIR}", "R4")),
        scanned("file.read", **path("config/.env.local")),
        scanned("git.push", **{"git.push_target": "origin main", "git.branch": "main"}),
        scanned("llm.request.completed", **{"llm.model": "m"}),  # not a guardable action
    ]  # fmt: skip


def test_counts_match_the_hand_computed_expectation() -> None:
    policy = parse_policy(DRAFT).policy
    assert policy is not None
    events = fixture_events()
    result = evaluate_events(policy, events)
    assert result.events_evaluated == 9 and result.events_skipped == 1 and result.truncated is None
    assert dict(result.by_decision) == {"allow": 4, "deny": 3, "require_approval": 2}
    assert dict(result.by_rule) == {
        ("deny-env-read", "deny"): 2,
        ("deny-r4", "deny"): 1,
        ("approve-risky-git", "require_approval"): 1,
        ("approve-push-main", "require_approval"): 1,
        (None, "allow"): 4,
    }
    # samples: the five non-allow hits in the order of the events, ids and decisions only
    got = [
        (h.event_id, h.capability, h.risk_class, h.decision.value, h.rule_id)
        for h in result.samples
    ]
    ask, deny = "require_approval", "deny"
    assert got == [
        (events[0].event_id, "shell.execute", "R3", ask, "approve-risky-git"),
        (events[2].event_id, "filesystem.read", None, deny, "deny-env-read"),
        (events[6].event_id, "shell.execute", "R4", deny, "deny-r4"),
        (events[7].event_id, "filesystem.read", None, deny, "deny-env-read"),
        (events[8].event_id, "github.push", None, ask, "approve-push-main"),
    ]
    assert evaluate_events(policy, events).samples == result.samples  # reproducible


def test_samples_are_capped_but_counts_are_not() -> None:
    policy = parse_policy(DRAFT).policy
    assert policy is not None
    events = [scanned("file.read", **path(".env")) for _ in range(100)]
    result = evaluate_events(policy, events, DryRunLimits(max_samples=7))
    assert len(result.samples) == 7 and result.by_decision["deny"] == 100


def test_the_time_budget_stops_the_loop_and_says_so() -> None:
    policy = parse_policy(DRAFT).policy
    assert policy is not None
    ticks = iter(range(1_000_000))
    events = [scanned("file.read", **path("a")) for _ in range(5000)]
    result = evaluate_events(
        policy, events, DryRunLimits(time_budget_seconds=3), clock=lambda: float(next(ticks))
    )
    assert result.truncated == "time_budget" and 0 < result.events_evaluated < 5000


def test_an_empty_draft_allows_everything() -> None:
    policy = parse_policy("version: 1\n").policy
    assert policy is not None
    result = evaluate_events(policy, fixture_events())
    assert dict(result.by_decision) == {"allow": 9} and result.samples == []
    deny_all = parse_policy("version: 1\ndefault: deny\n").policy
    assert deny_all is not None
    assert evaluate_events(deny_all, fixture_events()).by_decision["deny"] == 9


# ----------------------------------------------------------------------------------------- API


async def seed(api: Api) -> list[str]:
    """Two runs in project alpha holding the fixture actions; returns the event ids in order."""
    alpha = "alpha"
    run_a, run_b = make_run_ids(), make_run_ids()
    plan: list[tuple[dict[str, str], str, dict[str, Any]]] = [
        (run_a, "shell.command.started", shell(f"git push {SENTINEL}", "R3")),
        (run_a, "shell.command.started", shell("ls -la", "R0")),
        (run_a, "file.read", path(f"{SENTINEL}/.env")),
        (run_a, "file.read", path("src/app.py")),
        (run_a, "file.modified", path("src/app.py")),
        (run_a, "tool.call.started", {"tool.name": "github"}),
        (run_b, "shell.command.started", shell(f"rm -rf {TMP_DIR}", "R4")),
        (run_b, "file.read", path("config/.env.local")),
        (run_b, "git.push", {"git.push_target": "origin main", "git.branch": "main"}),
        (run_b, "shell.command.completed", {"shell.command": f"rm -rf {TMP_DIR}"}),
        (run_b, "llm.request.completed", {"llm.provider": "p", "llm.model": "m"}),
    ]  # fmt: skip
    events = [
        build_event(api.tenant, run, n=n, project=alpha, event_type=t, attributes=a)
        for n, (run, t, a) in enumerate(plan, 1)
    ]
    await ingest(api.engine, api.tenant, events)
    return [e.event_id for e in events]


async def dry_run(
    web: Api, headers: dict[str, str], document: str, project: str | None = None
) -> Any:
    return await web.client.post(
        "/v1/policies/dry-run",
        json={"project_id": project or web.tenant.projects["alpha"], "document": document},
        headers=headers,
    )


async def test_the_api_dry_run_matches_the_hand_computed_expectation(web: Api) -> None:
    headers = await person(web, "owner@acme.test", "OWNER")
    event_ids = await seed(web)
    response = await dry_run(web, headers, DRAFT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runs_considered"] == 2 and body["events_evaluated"] == 9
    assert body["events_skipped"] == 0 and body["truncated"] is None
    assert body["by_decision"] == {"allow": 4, "deny": 3, "require_approval": 2}
    assert {(r["rule_id"], r["decision"], r["count"]) for r in body["by_rule"]} == {
        ("deny-env-read", "deny", 2),
        ("deny-r4", "deny", 1),
        ("approve-risky-git", "require_approval", 1),
        ("approve-push-main", "require_approval", 1),
        (None, "allow", 4),
    }
    assert [r["count"] for r in body["by_rule"]] == sorted(
        (r["count"] for r in body["by_rule"]), reverse=True
    )
    assert len(body["samples"]) == 5
    expected = {event_ids[i] for i in (0, 2, 6, 7, 8)}
    assert {s["event_id"] for s in body["samples"]} == expected
    assert all(set(s) == {"run_id", "event_id", "capability", "risk_class", "decision", "rule_id"}
               for s in body["samples"])  # fmt: skip
    assert body["limits"]["max_events"] == 20_000
    again = await dry_run(web, headers, DRAFT)
    assert again.json() == body  # reproducible
    # the dry run changed nothing
    assert (await web.client.get("/v1/policies/versions", headers=headers)).json()["items"] == []


async def test_no_response_carries_content(web: Api) -> None:
    headers = await person(web, "owner@acme.test", "OWNER")
    await seed(web)
    pieces = [(await dry_run(web, headers, DRAFT)).text]
    # errors and other routes too: nothing the events held may appear
    pieces.append((await dry_run(web, headers, "version: 1\nbogus: 1\n")).text)
    pieces.append((await dry_run(web, headers, DRAFT, project=web.tenant.projects["beta"])).text)
    pieces.append((await web.client.get("/v1/policies/current", headers=headers)).text)
    for text in pieces:
        assert SENTINEL not in text
        for word in ("src/app.py", "rm -rf", "git push", ".env", TMP_DIR, "origin main"):
            assert word not in text, word
    assert json.loads(pieces[0])["samples"]


async def test_a_dry_run_is_scoped_to_the_project_and_the_workspace(web: Api) -> None:
    headers = await person(web, "owner@acme.test", "OWNER")
    await seed(web)  # alpha only
    beta = (await dry_run(web, headers, DRAFT, project=web.tenant.projects["beta"])).json()
    assert beta["events_evaluated"] == 0 and beta["runs_considered"] == 0
    for project in (web.other.projects["p"],):
        response = await dry_run(web, headers, DRAFT, project=project)
        assert (
            response.status_code == 404 and response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
        )
    # another workspace's owner replaying the same draft sees none of acme's events
    globex = await person(web, "boss@globex.test", "OWNER", workspace="globex")
    theirs = await dry_run(web, globex, DRAFT, project=web.other.projects["p"])
    assert theirs.json()["events_evaluated"] == 0


async def test_an_invalid_draft_is_a_422_before_any_events_are_read(web: Api) -> None:
    headers = await person(web, "owner@acme.test", "OWNER")
    response = await dry_run(web, headers, "version: 1\npolicies: 3\n")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["issues"][0]["code"] == "wrong_type"


async def test_bounds_on_events_and_runs_are_reported(web: Api) -> None:
    headers = await person(web, "owner@acme.test", "OWNER")
    await seed(web)
    engine = web.app.state.engine
    web.app.state.policies = PolicyApiService(engine, limits=DryRunLimits(max_events=4))
    body = (await dry_run(web, headers, DRAFT)).json()
    assert body["truncated"] == "events" and body["events_evaluated"] == 4
    web.app.state.policies = PolicyApiService(engine, limits=DryRunLimits(max_runs=1))
    body = (await dry_run(web, headers, DRAFT)).json()
    assert body["truncated"] == "runs" and body["runs_considered"] == 1
    web.app.state.policies = PolicyApiService(engine, dry_runs_per_minute=2)
    codes = [(await dry_run(web, headers, DRAFT)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_a_ten_thousand_event_run_is_bounded_and_fast(
    web: Api, capsys: pytest.CaptureFixture[str]
) -> None:
    headers = await person(web, "owner@acme.test", "OWNER")
    run = make_run_ids()
    kinds = (
        ("shell.command.started", lambda i: shell(f"git status {i}", "R1")),
        ("file.read", lambda i: {"file.path": f"src/pkg{i % 50}/mod{i}.py"}),
        ("file.modified", lambda i: {"file.path": f"src/pkg{i % 50}/mod{i}.py"}),
        ("tool.call.started", lambda i: {"tool.name": f"tool{i % 7}"}),
        ("llm.request.completed", lambda i: {"llm.provider": "p", "llm.model": "m"}),
    )  # fmt: skip
    events = []
    for n in range(1, 10_001):
        event_type, attrs = kinds[n % len(kinds)]
        events.append(
            build_event(
                web.tenant, run, n=n, project="alpha", event_type=event_type, attributes=attrs(n)
            )
        )
    for start in range(0, len(events), 1000):
        await ingest(web.engine, web.tenant, events[start : start + 1000])
    rules = "".join(
        f"  - id: r{i}\n    when: {{capability: filesystem.read, resource: 'src/pkg{i}/**'}}\n"
        "    decision: deny\n"
        for i in range(60)
    )
    draft = "version: 1\npolicies:\n" + rules
    started = time.perf_counter()
    response = await dry_run(web, headers, draft)
    elapsed = time.perf_counter() - started
    body = response.json()
    assert response.status_code == 200 and body["truncated"] is None
    assert body["events_evaluated"] == 8000  # four of every five events are guardable
    # every file.read (n % 5 == 1) is in a package the draft covers; nothing else is denied
    assert body["by_decision"] == {"allow": 6000, "deny": 2000, "require_approval": 0}
    assert elapsed < 10.0
    with capsys.disabled():
        print(f"\ndry run over 10,000 events, 60 rules: {elapsed:.2f}s")
