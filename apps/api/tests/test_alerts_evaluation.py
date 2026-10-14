"""Rule evaluation (ADR-150): the producers only enqueue, the job matches and writes one
notification per (rule, destination, event), dedupe and cooldown hold, and nothing but ids, names
and fixed strings reaches a payload."""

import json
import logging
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, to_uuid
from sqlalchemy import text

from abb_api.alerts import model as m
from abb_api.alerts.evaluate import evaluate_event, rule_matches, spend_check, sweep
from abb_api.alerts.payload import DETAIL_KEYS, TOP_LEVEL_KEYS
from abb_api.alerts.repository import DestinationRepository, RuleRepository
from abb_api.jobs.outbox import EVALUATE_ALERTS, OutboxRepository
from tests.alerts_helpers import (
    post_run,
    rows,
    run_events,
    seed_destination,
    seed_rule,
    utc,
)
from tests.api_fixtures import NOW, Api
from tests.approvals.helpers import ask, publish
from tests.ingest_helpers import wire_event
from tests.security_helpers import PLANT, planted_run

CAP = 25


async def destination(api: Api, name: str = "pager", **kwargs: Any) -> uuid.UUID:
    return await seed_destination(api.engine, api.tenant.context, name=name, **kwargs)


async def rule(api: Api, destinations: list[uuid.UUID], **kwargs: Any) -> uuid.UUID:
    return await seed_rule(api.engine, api.tenant.context, destinations, **kwargs)


# ---------------------------------------------------------------- run_failed


async def test_a_failed_timed_out_or_blocked_run_makes_one_notification_per_destination(
    api: Api,
) -> None:
    one, two = (
        await destination(api, "one"),
        await destination(api, "two", url="https://example.org/h"),
    )
    rule_id = await rule(api, [one, two], trigger=m.RUN_FAILED)
    runs = {k: await post_run(api, k) for k in ("FAILED", "TIMED_OUT", "BLOCKED", "SUCCESS")}
    await api.drain()
    found = await rows(api)
    assert len(found) == 6  # three bad runs x two destinations; the successful run alerts nobody
    by_run = {str(r.run_id): r for r in found}
    assert {public(IdKind.RUN, r.run_id) for r in found} == {
        runs[k] for k in ("FAILED", "TIMED_OUT", "BLOCKED")
    }
    assert all(r.status == "pending" and r.rule_id == rule_id and r.attempts == 0 for r in found)
    assert len({r.id for r in found}) == 6 and by_run
    severities = {r.payload_json["details"]["run_status"]: r.severity for r in found}
    assert severities == {"FAILED": "high", "TIMED_OUT": "high", "BLOCKED": "warning"}


def public(kind: IdKind, value: uuid.UUID) -> str:
    from abb_api.ids import public_id

    return public_id(kind, value)


async def test_resummarising_and_late_events_do_not_alert_twice(api: Api) -> None:
    await rule(api, [await destination(api)], trigger=m.RUN_FAILED)
    run_id = await post_run(api, "FAILED")
    await api.drain()
    assert len(await rows(api)) == 1
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_summarize([to_uuid(run_id)])
    await api.drain()
    late = wire_event(
        {"run_id": run_id, "trace_id": (await api_trace(api, run_id))},
        20,
        event_type="tool.call.completed",
        agent_id="coding-agent",
    )
    assert (await api.post_batch([late])).status_code == 202
    await api.drain()
    assert len(await rows(api)) == 1


async def api_trace(api: Api, run_id: str) -> str:
    from abb_api.ids import public_id

    async with api.engine.connect() as conn:
        trace = (
            await conn.execute(
                text("SELECT trace_id FROM runs WHERE id = :i"), {"i": to_uuid(run_id)}
            )
        ).scalar_one()
    return public_id(IdKind.TRACE, trace)


async def test_run_filters_are_respected(api: Api) -> None:
    d = await destination(api)
    only_failed = await rule(
        api, [d], name="failed", trigger=m.RUN_FAILED, filters={"run_statuses": ["FAILED"]}
    )
    beta = await rule(
        api, [d], name="beta", trigger=m.RUN_FAILED, project_id=api.tenant.project_uuids["beta"]
    )
    alpha = await rule(
        api,
        [d],
        name="alpha",
        trigger=m.RUN_FAILED,
        project_id=api.tenant.project_uuids["alpha"],
        filters={"agent": "planner"},
    )
    await rule(api, [d], name="muted", trigger=m.RUN_FAILED, enabled=False)
    await rule(
        api, [d], name="other trigger", trigger=m.SECURITY_FINDING, filters={"min_severity": "info"}
    )
    await post_run(api, "FAILED")
    await post_run(api, "TIMED_OUT")
    await post_run(api, "FAILED", agent="planner")
    await api.drain()
    found = await rows(api)
    counts: dict[uuid.UUID, int] = {}
    for r in found:
        counts[r.rule_id] = counts.get(r.rule_id, 0) + 1
    assert counts == {only_failed: 2, alpha: 1}  # beta's project saw nothing; muted ignored
    assert beta not in counts


async def test_a_run_that_is_no_longer_failed_when_the_job_runs_alerts_nobody(api: Api) -> None:
    await rule(api, [await destination(api)], trigger=m.RUN_FAILED)
    run_id = await post_run(api, "SUCCESS")
    await api.drain()
    async with api.engine.begin() as conn:
        outcome = await evaluate_event(
            conn, api.tenant.context, m.RUN_FAILED, to_uuid(run_id), now=NOW, skipped_cap=CAP
        )
    assert (outcome.created, outcome.skipped) == (0, 0)


async def test_the_hook_only_enqueues_a_small_job_keyed_by_the_run(api: Api) -> None:
    await rule(api, [await destination(api)], trigger=m.RUN_FAILED)
    run_id = await post_run(api, "FAILED")
    # Run the summarizer only: the evaluation job is waiting, nothing is matched yet.
    claimed = await api.worker.run_once()
    assert claimed >= 1
    assert await rows(api) == []
    async with api.engine.connect() as conn:
        jobs = (
            await conn.execute(
                text(
                    "SELECT dedupe_key, payload FROM outbox_jobs "
                    "WHERE job_type = :t AND status = 'pending'"
                ),
                {"t": EVALUATE_ALERTS},
            )
        ).all()
    assert len(jobs) == 1
    assert jobs[0].dedupe_key == f"run_failed:{to_uuid(run_id)}"
    assert jobs[0].payload == {"trigger": "run_failed", "subject_id": str(to_uuid(run_id))}
    await api.drain()
    assert len(await rows(api)) == 1


# ---------------------------------------------------------------- security findings


async def stored_security(api: Api) -> list[Any]:
    async with api.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT * FROM findings WHERE detector LIKE 'sec.%' ORDER BY id")
        )
        return list(result)


async def test_each_security_finding_at_or_above_the_severity_alerts_once_and_rebuilds_not_again(
    api: Api,
) -> None:
    d = await destination(api)
    await rule(
        api, [d], name="critical", trigger=m.SECURITY_FINDING, filters={"min_severity": "critical"}
    )
    await rule(api, [d], name="any", trigger=m.SECURITY_FINDING, filters={"min_severity": "info"})
    run, _ = planted_run("acme")
    assert (await api.post_batch(run.wires)).status_code == 202
    await api.drain()
    findings = await stored_security(api)
    critical = [f for f in findings if f.severity == "critical"]
    assert critical and len(findings) > len(critical)
    found = await rows(api)
    assert len(found) == len(critical) + len(findings)
    assert {r.finding_id for r in found} == {f.id for f in findings}
    for r in found:
        assert r.payload_json["details"]["finding_severity"] == r.severity
    before = {r.id for r in found}
    # A rebuild (the findings job runs again over the same events) announces nothing new.
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run.run_id)])
    await api.drain()
    assert {r.id for r in await rows(api)} == before


async def evaluate_jobs(api: Api) -> int:
    async with api.engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text("SELECT count(*) FROM outbox_jobs WHERE job_type = 'evaluate_alerts'")
                )
            ).scalar_one()
        )


async def test_only_a_new_or_worse_finding_is_announced_by_the_findings_job(api: Api) -> None:
    await rule(
        api, [await destination(api)], trigger=m.SECURITY_FINDING, filters={"min_severity": "info"}
    )
    run, _ = planted_run("acme")
    assert (await api.post_batch(run.wires)).status_code == 202
    await api.drain()
    announced = await evaluate_jobs(api)
    first = await rows(api)
    assert announced == len(first) > 0
    # Unchanged findings rewritten by a rebuild are not even enqueued.
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run.run_id)])
    await api.drain()
    assert await evaluate_jobs(api) == announced
    # A finding that used to be milder and is now worse is announced (once).
    target = next(f for f in await stored_security(api) if f.severity in ("critical", "high"))
    async with api.engine.begin() as conn:
        await conn.execute(
            text("UPDATE findings SET severity = 'warning' WHERE id = :i"), {"i": target.id}
        )
        await conn.execute(
            text("DELETE FROM alert_notifications WHERE finding_id = :i"), {"i": target.id}
        )
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run.run_id)])
    await api.drain()
    assert await evaluate_jobs(api) == announced + 1
    again = [r for r in await rows(api) if r.finding_id == target.id]
    assert len(again) == 1 and again[0].severity == target.severity


async def test_a_finding_whose_severity_rises_gets_a_new_alert_with_the_same_one_deduped(
    api: Api,
) -> None:
    await rule(
        api, [await destination(api)], trigger=m.SECURITY_FINDING, filters={"min_severity": "info"}
    )
    run, _ = planted_run("acme")
    assert (await api.post_batch(run.wires)).status_code == 202
    await api.drain()
    target = next(f for f in await stored_security(api) if f.severity in ("critical", "high"))
    keys = {r.dedupe_key for r in await rows(api) if r.finding_id == target.id}
    assert keys == {f"{target.id}:{target.detector_version}:{target.severity}"}
    now = utc(2026, 10, 8, 9)

    async def run_job() -> Any:
        async with api.engine.begin() as conn:
            return await evaluate_event(
                conn, api.tenant.context, m.SECURITY_FINDING, target.id, now=now, skipped_cap=CAP
            )

    assert (await run_job()).created == 0  # same id, version and severity: deduped
    lower = "medium" if target.severity == "high" else "high"
    async with api.engine.begin() as conn:  # the stored finding is now at a different severity
        await conn.execute(
            text("UPDATE findings SET severity = :s WHERE id = :i"), {"s": lower, "i": target.id}
        )
    assert (await run_job()).created == 1
    assert (await run_job()).created == 0


# ---------------------------------------------------------------- approvals


async def test_an_approval_request_alerts_once_per_rule_and_destination(api: Api) -> None:
    await publish(api)
    one, two = (
        await destination(api, "one"),
        await destination(api, "two", url="https://example.org/h"),
    )
    both = await rule(api, [one, two], name="all", trigger=m.APPROVAL_REQUESTED)
    other_agent = await rule(
        api,
        [one],
        name="other agent",
        trigger=m.APPROVAL_REQUESTED,
        filters={"agent": "someone-else"},
    )
    body, approval_id = await ask(api)
    again, same_id = await ask(api, body["run_id"])  # the same open request: not a second approval
    await api.drain()
    found = await rows(api)
    assert same_id == approval_id and again
    assert len(found) == 2 and {r.rule_id for r in found} == {both}
    assert other_agent not in {r.rule_id for r in found}
    assert {public(IdKind.APPROVAL, r.approval_id) for r in found} == {approval_id}
    await ask(api, arguments={"remote": "origin", "force": True})
    await api.drain()
    assert len(await rows(api)) == 4


async def test_an_expiring_approval_alerts_once_when_the_lead_time_is_reached(api: Api) -> None:
    await publish(api)
    d = await destination(api)
    await rule(api, [d], trigger=m.APPROVAL_EXPIRING, filters={"expiring_lead_seconds": 600})
    _, approval_id = await ask(api)  # asked at NOW, valid for 15 minutes
    ten_before = NOW + timedelta(minutes=5, seconds=1)  # 9m59s left: inside the lead
    async with api.engine.begin() as conn:
        early = await sweep(
            conn, api.tenant.context, now=NOW + timedelta(minutes=4), skipped_cap=CAP
        )
    assert early.created == 0  # 11 minutes left
    async with api.engine.begin() as conn:
        due = await sweep(conn, api.tenant.context, now=ten_before, skipped_cap=CAP)
        again = await sweep(
            conn, api.tenant.context, now=ten_before + timedelta(minutes=1), skipped_cap=CAP
        )
    assert (due.created, again.created) == (1, 0)
    (row,) = [r for r in await rows(api) if r.trigger == "approval_expiring"]
    assert public(IdKind.APPROVAL, row.approval_id) == approval_id
    assert row.severity == "warning" and row.payload_json["details"]["seconds_to_expiry"] == 599
    async with (
        api.engine.begin() as conn
    ):  # once it has expired there is nothing left to warn about
        late = await sweep(
            conn, api.tenant.context, now=NOW + timedelta(minutes=16), skipped_cap=CAP
        )
    assert late.created == 0


async def test_a_decided_approval_is_not_warned_about(api: Api) -> None:
    await publish(api)
    await rule(
        api,
        [await destination(api)],
        trigger=m.APPROVAL_EXPIRING,
        filters={"expiring_lead_seconds": 900},
    )
    await ask(api)
    async with api.engine.begin() as conn:
        await conn.execute(
            text("UPDATE approvals SET state = 'CANCELLED', closed_at = :n"),
            {"n": NOW + timedelta(seconds=1)},
        )
    async with api.engine.begin() as conn:
        out = await sweep(conn, api.tenant.context, now=NOW + timedelta(minutes=2), skipped_cap=CAP)
    assert out.created == 0


# ---------------------------------------------------------------- spend


async def test_a_spend_rule_fires_once_per_day_at_the_crossing_and_not_before(api: Api) -> None:
    d = await destination(api)
    rule_id = await rule(
        api,
        [d],
        trigger=m.SPEND_OVER,
        filters={"threshold_usd": 10.0, "period": "day"},
        project_id=api.tenant.project_uuids["alpha"],
    )
    beta = await rule(
        api,
        [d],
        name="beta",
        trigger=m.SPEND_OVER,
        filters={"threshold_usd": 1.0, "period": "day"},
        project_id=api.tenant.project_uuids["beta"],
    )
    by_agent = await rule(
        api,
        [d],
        name="agent",
        trigger=m.SPEND_OVER,
        filters={"threshold_usd": 7.0, "period": "day", "agent": "other-agent"},
    )
    day = utc(2026, 10, 7, 20)  # the day the fixture's events happened (UTC)
    await post_run(api, "SUCCESS", cost=6.0)
    await api.drain()

    async def check(at: Any = day) -> int:
        async with api.engine.begin() as conn:
            return (await spend_check(conn, api.tenant.context, now=at, skipped_cap=CAP)).created

    assert await check() == 0  # 6 of 10
    await post_run(api, "SUCCESS", cost=4.0)
    await api.drain()
    assert await check() == 0  # exactly 10 is not over
    await post_run(api, "SUCCESS", cost=0.5)
    await api.drain()
    assert await check() == 1  # 10.5: crossed
    assert await check() == 0 and await check(day + timedelta(hours=2)) == 0  # once that day
    await post_run(api, "SUCCESS", cost=100.0)
    await api.drain()
    assert await check() == 0
    next_day = day + timedelta(days=1)
    assert await check(next_day) == 0  # no spend yet that day
    (only,) = await rows(api)
    assert only.rule_id == rule_id and only.dedupe_key == f"{rule_id}:2026-10-07"
    assert only.payload_json["details"] == {
        "day": "2026-10-07",
        "spend_usd": 10.5,
        "threshold_usd": 10.0,
        "period": "day",
    }
    assert beta != rule_id and by_agent != rule_id


async def test_spend_numbers_are_the_ones_the_cost_page_shows(api: Api) -> None:
    from abb_api.alerts.spend import SpendReader

    await post_run(api, "SUCCESS", cost=2.5)
    await post_run(api, "FAILED", cost=1.25)
    await api.drain()
    await api.drain()
    async with api.engine.connect() as conn:
        spent = await SpendReader(conn, api.tenant.context).day_total(
            utc(2026, 10, 7).date(), project_id=None, agent_slug=None
        )
    response = await api.get(
        "/v1/analytics/cost",
        token="wide_reader",
        **{"from": "2026-10-07T00:00:00Z", "to": "2026-10-07T23:59:59Z"},
    )
    assert response.status_code == 200
    assert (
        float(spent)
        == pytest.approx(response.json()["headline"]["total_usd"])
        == pytest.approx(3.75)
    )


# ---------------------------------------------------------------- cooldown, pause, caps


async def make_failed_runs(api: Api, n: int) -> list[str]:
    ids = [await post_run(api, "FAILED") for _ in range(n)]
    await api.drain()  # no rule yet: nothing is written
    assert await rows(api) == []
    return ids


async def evaluate(api: Api, run_id: str, at: Any, cap: int = CAP) -> Any:
    async with api.engine.begin() as conn:
        return await evaluate_event(
            conn, api.tenant.context, m.RUN_FAILED, to_uuid(run_id), now=at, skipped_cap=cap
        )


async def test_a_cooldown_suppresses_new_alerts_and_records_them_as_skipped(api: Api) -> None:
    run_ids = await make_failed_runs(api, 3)
    d = await destination(api)
    other = await destination(api, "other", url="https://example.org/h")
    rule_id = await rule(api, [d, other], trigger=m.RUN_FAILED, cooldown_seconds=3600)
    t0 = utc(2026, 10, 8, 9)
    first = await evaluate(api, run_ids[0], t0)
    second = await evaluate(api, run_ids[1], t0 + timedelta(minutes=10))
    third = await evaluate(api, run_ids[2], t0 + timedelta(minutes=61))
    assert (first.created, first.skipped) == (2, 0)
    assert (second.created, second.skipped) == (0, 2)
    assert (third.created, third.skipped) == (
        2,
        0,
    )  # the window is measured from the last real alert
    found = await rows(api)
    skipped = [r for r in found if r.status == "skipped"]
    assert len(skipped) == 2 and {r.skip_reason for r in skipped} == {"cooldown"}
    assert {str(r.run_id) for r in skipped} == {str(to_uuid(run_ids[1]))}
    assert all(r.rule_id == rule_id for r in found)
    # The skipped event's key is spent: evaluating it again later does not alert after all.
    again = await evaluate(api, run_ids[1], t0 + timedelta(hours=5))
    assert (again.created, again.skipped) == (0, 0)


async def test_cooldown_is_per_destination_and_per_rule(api: Api) -> None:
    run_ids = await make_failed_runs(api, 2)
    d = await destination(api)
    await rule(api, [d], name="a", trigger=m.RUN_FAILED, cooldown_seconds=3600)
    await rule(api, [d], name="b", trigger=m.RUN_FAILED, cooldown_seconds=0)
    t0 = utc(2026, 10, 8, 9)
    await evaluate(api, run_ids[0], t0)
    second = await evaluate(api, run_ids[1], t0 + timedelta(minutes=1))
    assert (second.created, second.skipped) == (1, 1)  # b fires, a is cooling down


async def test_suppressed_alerts_beyond_the_cap_are_counted_not_stored(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    run_ids = await make_failed_runs(api, 5)
    await rule(api, [await destination(api)], trigger=m.RUN_FAILED, cooldown_seconds=3600)
    t0 = utc(2026, 10, 8, 9)
    await evaluate(api, run_ids[0], t0, cap=2)
    outcomes = [
        await evaluate(api, r, t0 + timedelta(seconds=n + 1), cap=2)
        for n, r in enumerate(run_ids[1:])
    ]
    assert [o.skipped for o in outcomes] == [1, 1, 0, 0]
    assert [o.dropped for o in outcomes] == [0, 0, 1, 1]
    assert len([r for r in await rows(api) if r.status == "skipped"]) == 2
    assert "alert suppressed beyond the history cap" in caplog.text


async def test_a_paused_destination_gets_a_skipped_row_not_a_pending_one(api: Api) -> None:
    run_ids = await make_failed_runs(api, 1)
    paused = await destination(api, "paused", state="paused")
    await rule(api, [paused], trigger=m.RUN_FAILED)
    out = await evaluate(api, run_ids[0], utc(2026, 10, 8, 9))
    assert (out.created, out.skipped) == (0, 1)
    (row,) = await rows(api)
    assert (row.status, row.skip_reason) == ("skipped", "destination_paused")


async def test_a_redelivered_job_inserts_nothing_new(api: Api) -> None:
    run_ids = await make_failed_runs(api, 1)
    await rule(api, [await destination(api)], trigger=m.RUN_FAILED)
    t0 = utc(2026, 10, 8, 9)
    first = await evaluate(api, run_ids[0], t0)
    second = await evaluate(api, run_ids[0], t0 + timedelta(seconds=30))
    assert (first.created, second.created, second.skipped) == (1, 0, 0)
    assert len(await rows(api)) == 1


# ---------------------------------------------------------------- isolation


async def test_nothing_crosses_workspaces(api: Api) -> None:
    mine = await destination(api)
    await rule(api, [mine], trigger=m.RUN_FAILED)
    their_dest = await seed_destination(api.engine, api.other.context, name="theirs")
    their_rule = await seed_rule(api.engine, api.other.context, [their_dest], trigger=m.RUN_FAILED)
    await post_run(api, "FAILED")  # acme's run
    await post_run(api, "FAILED", token="other")  # globex's run
    await api.drain()
    found = await rows(api)
    assert len(found) == 2
    for r in found:
        if r.rule_id == their_rule:
            assert r.workspace_id == api.other.context.workspace_id
        else:
            assert r.workspace_id == api.tenant.context.workspace_id
    async with api.engine.begin() as conn:  # the matcher of one tenant never sees the other's rules
        targets = await RuleRepository(conn, api.tenant.context).enabled_targets(m.RUN_FAILED)
        assert len(targets) == 1 and targets[0].rule.id != their_rule
        assert await DestinationRepository(conn, api.tenant.context).get(their_dest) is None


async def test_the_database_refuses_a_rule_that_points_at_another_workspaces_destination(
    api: Api,
) -> None:
    from sqlalchemy.exc import IntegrityError

    theirs = await seed_destination(api.engine, api.other.context, name="theirs")
    mine = await destination(api)
    rule_id = await rule(api, [mine], trigger=m.RUN_FAILED)
    with pytest.raises(IntegrityError):
        async with api.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO alert_rule_destinations (workspace_id, rule_id, destination_id) "
                    "VALUES (:w, :r, :d)"
                ),
                {"w": api.tenant.context.workspace_id, "r": rule_id, "d": theirs},
            )


# ---------------------------------------------------------------- content-free payloads


def all_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k), *all_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in all_strings(v)]
    return [str(value)]


async def test_no_planted_content_reaches_any_payload_row_log_or_audit_entry(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    """A sentinel sits in every path, command, host, argument, error message and run name that a
    trigger could be tempted to quote; it appears nowhere an alert is stored, logged or audited."""
    caplog.set_level(logging.DEBUG)
    await publish(api)
    d = await destination(api)
    await rule(api, [d], name="r1", trigger=m.RUN_FAILED)
    await rule(api, [d], name="r2", trigger=m.SECURITY_FINDING, filters={"min_severity": "info"})
    await rule(api, [d], name="r3", trigger=m.APPROVAL_REQUESTED)
    await rule(
        api, [d], name="r4", trigger=m.APPROVAL_EXPIRING, filters={"expiring_lead_seconds": 900}
    )
    await rule(
        api, [d], name="r5", trigger=m.SPEND_OVER, filters={"threshold_usd": 1.0, "period": "day"}
    )

    for kind in ("FAILED", "TIMED_OUT", "BLOCKED"):
        run_id, events = run_events(kind, message=f"{PLANT} fatal /home/{PLANT}/secret", cost=3.0)
        events[0]["attributes"] = {"run.name": f"{PLANT}-run-name"}
        assert (await api.post_batch(events)).status_code == 202
        assert run_id
    run, _ = planted_run("acme")
    assert (await api.post_batch(run.wires)).status_code == 202
    await ask(api, arguments={"cmd": f"curl {PLANT}.example.net", "path": f"/srv/{PLANT}"})
    await api.drain()
    async with api.engine.begin() as conn:
        await sweep(conn, api.tenant.context, now=utc(2026, 10, 7, 12, 10), skipped_cap=CAP)

    found = await rows(api)
    assert {r.trigger for r in found} == set(m.TRIGGERS)
    for r in found:
        payload = r.payload_json
        assert set(payload) <= TOP_LEVEL_KEYS, set(payload) - TOP_LEVEL_KEYS
        assert set(payload["details"]) <= DETAIL_KEYS
        assert payload["title"] and payload["link_path"].startswith("/w/")
        assert PLANT not in json.dumps(payload)
    async with api.engine.connect() as conn:
        everywhere = [
            ("alert_notifications", "SELECT t::text FROM alert_notifications t"),
            (
                "outbox_jobs",
                "SELECT t::text FROM outbox_jobs t "
                "WHERE job_type IN ('evaluate_alerts', 'alert_sweep')",
            ),
            ("audit_log", "SELECT t::text FROM audit_log t"),
        ]
        for table, query in everywhere:
            for (blob,) in await conn.execute(text(query)):
                assert PLANT not in blob, table
    assert PLANT not in caplog.text
    # The scan is potent: the sentinel is in what the product stored about these runs.
    async with api.engine.connect() as conn:
        stored = (
            await conn.execute(text("SELECT string_agg(t::text, ' ') FROM events t"))
        ).scalar_one()
    assert PLANT in stored


# ---------------------------------------------------------------- the matcher on its own


def subject(**kwargs: Any) -> m.Subject:
    base: dict[str, Any] = {
        "trigger": m.SECURITY_FINDING,
        "dedupe_key": "k",
        "severity": "high",
        "workspace_id": uuid.uuid4(),
        "workspace_slug": "acme",
        "project_id": uuid.UUID(int=1),
        "project_slug": "alpha",
        "project_name": "Alpha",
        "agent_slug": "coding-agent",
        "agent_name": None,
        "finding_severity": "high",
    }
    return m.Subject(**{**base, **kwargs})


def a_rule(trigger: str, project: uuid.UUID | None = None, **filters: Any) -> m.RuleRow:
    now = utc(2026, 10, 8)
    return m.RuleRow(
        id=uuid.uuid4(), project_id=project, name="r", trigger=trigger, filters=filters,
        cooldown_seconds=0, enabled=True, last_fired_at=None, created_by="t", created_at=now,
        updated_by="t", updated_at=now,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("minimum", "severity", "expected"),
    [
        ("info", "info", True),
        ("medium", "warning", False),
        ("medium", "medium", True),
        ("high", "medium", False),
        ("high", "critical", True),
        ("critical", "high", False),
    ],
)
def test_the_severity_floor_uses_the_real_vocabulary_order(
    minimum: str, severity: str, expected: bool
) -> None:
    rule = a_rule(m.SECURITY_FINDING, min_severity=minimum)
    assert rule_matches(rule, subject(finding_severity=severity)) is expected


def test_project_agent_status_and_lead_filters() -> None:
    assert rule_matches(a_rule(m.SECURITY_FINDING, min_severity="info"), subject())
    elsewhere = a_rule(m.SECURITY_FINDING, project=uuid.UUID(int=2), min_severity="info")
    assert not rule_matches(elsewhere, subject())
    other_agent = a_rule(m.SECURITY_FINDING, min_severity="info", agent="planner")
    assert not rule_matches(other_agent, subject())
    run = subject(trigger=m.RUN_FAILED, run_status="TIMED_OUT", finding_severity=None)
    assert rule_matches(a_rule(m.RUN_FAILED), run)  # no filter: all three statuses
    assert rule_matches(a_rule(m.RUN_FAILED, run_statuses=["TIMED_OUT"]), run)
    assert not rule_matches(a_rule(m.RUN_FAILED, run_statuses=["FAILED"]), run)
    expiring = subject(trigger=m.APPROVAL_EXPIRING, seconds_to_expiry=300.0)
    assert rule_matches(a_rule(m.APPROVAL_EXPIRING, expiring_lead_seconds=300), expiring)
    assert not rule_matches(a_rule(m.APPROVAL_EXPIRING, expiring_lead_seconds=299), expiring)
    off = a_rule(m.RUN_FAILED)
    assert not rule_matches(replace(off, enabled=False), run)
    assert not rule_matches(a_rule(m.SECURITY_FINDING, min_severity="info"), run)  # wrong trigger
