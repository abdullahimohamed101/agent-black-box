"""The alerting management API (ADR-150): destinations, rules, preview and history, with roles,
validation per trigger, secrets that never leave, audit, limits and rate limits."""

import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from abb_event_schema.ids import IdKind, new_id, to_uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.alerts import model as m
from abb_api.jobs.outbox import ALERT_SWEEP
from tests.alerts_helpers import post_run, rows, seed_destination, seed_rule
from tests.api_fixtures import NOW, WEB_ORIGIN, Api, build_api, person
from tests.approvals.helpers import ask, member, publish
from tests.conftest import make_settings
from tests.security_helpers import planted_run
from tests.test_runs_api import error

HOOK = "https://hooks.example.com/services/T000/B000/SENTINELSECRETTOKEN1234"
SLACK = "https://hooks.slack.com/services/T0/B0/SLACKSENTINELTOKEN0000"
PLAIN = ("SENTINELSECRETTOKEN", "SLACKSENTINELTOKEN", "T000/B000", "oncall-person")


@pytest.fixture
async def mail(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    """The web API with an SMTP relay configured, so email destinations can be created."""
    settings = make_settings(
        database_url,
        web_origin=WEB_ORIGIN,
        smtp_host="smtp.example.com",
        smtp_from="abb@example.com",
    )
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


async def owner(api: Api) -> dict[str, str]:
    return await person(api, "owner@acme.test", "OWNER")


async def create_destination(
    api: Api, headers: dict[str, str], name: str = "pager", **body: Any
) -> httpx.Response:
    payload = {"name": name, "type": "webhook", "target": {"url": HOOK}, **body}
    return await api.client.post("/v1/alerts/destinations", json=payload, headers=headers)


async def create_rule(
    api: Api, headers: dict[str, str], destination_ids: list[str], **body: Any
) -> httpx.Response:
    payload = {"name": "failures", "trigger": "run_failed", "destination_ids": destination_ids}
    return await api.client.post("/v1/alerts/rules", json={**payload, **body}, headers=headers)


async def audit(api: Api, like: str = "alert.%") -> list[Any]:
    async with api.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT * FROM audit_log WHERE action LIKE :a ORDER BY id"), {"a": like}
        )
        return list(result)


# ---------------------------------------------------------------- destinations


async def test_creating_a_webhook_returns_the_signing_secret_once_and_never_the_url(
    web: Api,
) -> None:
    headers = await owner(web)
    created = await create_destination(web, headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"].startswith("dst_") and body["type"] == "webhook" and body["state"] == "active"
    assert body["signing_secret"].startswith("whsec_") and len(body["signing_secret"]) >= 40
    assert body["masked_target"] == "https://hooks.example.com/…/1234"
    assert body["rule_count"] == 0 and body["consecutive_failures"] == 0
    assert HOOK not in created.text and "SENTINELSECRETTOKEN" not in created.text
    fetched = await web.client.get(f"/v1/alerts/destinations/{body['id']}", headers=headers)
    listed = await web.client.get("/v1/alerts/destinations", headers=headers)
    for response in (fetched, listed):
        assert response.status_code == 200
        assert "signing_secret" not in response.text and body["signing_secret"] not in response.text
        assert "SENTINELSECRETTOKEN" not in response.text and HOOK not in response.text
    assert listed.json()["email_configured"] is False
    (entry,) = await audit(web, "alert.destination.create")
    assert entry.resource_id == body["id"] and entry.details == {"type": "webhook"}


async def test_slack_has_no_signing_secret_and_email_is_refused_without_smtp(web: Api) -> None:
    headers = await owner(web)
    slack = await create_destination(web, headers, "slack", type="slack", target={"url": SLACK})
    assert slack.status_code == 201 and slack.json()["signing_secret"] is None
    unavailable = await web.client.post(
        "/v1/alerts/destinations",
        json={
            "name": "mail",
            "type": "email",
            "target": {"recipients": ["oncall-person@example.com"]},
        },
        headers=headers,
    )
    assert error(unavailable, 422, "EMAIL_NOT_CONFIGURED")
    assert "oncall-person" not in unavailable.text
    listed = await web.client.get("/v1/alerts/destinations", headers=headers)
    assert listed.json()["email_configured"] is False


async def test_email_destinations_are_masked_validated_and_never_echo_an_address(
    mail: Api,
) -> None:
    headers = await owner(mail)
    sent = await mail.client.post(
        "/v1/alerts/destinations",
        json={
            "name": "mail",
            "type": "email",
            "target": {"recipients": ["Oncall-Person@Example.com", "second@example.org"]},
        },
        headers=headers,
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["masked_target"] == "O***@example.com, s***@example.org"
    assert sent.json()["signing_secret"] is None
    assert "oncall-person" not in sent.text.lower()
    for recipients, reason in (
        ([], "recipients_required"),
        (["not-an-address"], "recipient_invalid"),
    ):
        bad = await mail.client.post(
            "/v1/alerts/destinations",
            json={"name": "x", "type": "email", "target": {"recipients": recipients}},
            headers=headers,
        )
        assert error(bad, 422, "DESTINATION_TARGET_INVALID")["details"] == {"reason": reason}
        assert "not-an-address" not in bad.text
    listed = await mail.client.get("/v1/alerts/destinations", headers=headers)
    assert listed.json()["email_configured"] is True
    assert "oncall-person" not in listed.text.lower()
    audited = json.dumps([e.details for e in await audit(mail)])
    assert "oncall-person" not in audited.lower() and "example.org" not in audited


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ({"url": "http://hooks.example.com/x"}, "not_https"),
        ({"url": "https://user:pw@hooks.example.com/x"}, "credentials_in_url"),
        ({"url": "https://localhost/x"}, "host_internal"),
        ({"url": "https://169.254.169.254/latest"}, "ip_not_public"),
        ({"url": "https://[::1]/x"}, "ip_not_public"),
        ({"url": "https://127.1/x"}, "ip_literal_form"),
        ({"url": "https://intranet/x"}, "host_internal"),
        ({"url": "https://hooks.example.com:8443/x"}, "port_not_allowed"),
        ({"url": "https://hooks.example.com:6379/x"}, "port_not_allowed"),
        ({"url": "https://hooks.example.com:0/x"}, "port_invalid"),
        ({"recipients": ["a@example.com"]}, "target_shape"),
        ({}, "target_shape"),
    ],
)
async def test_bad_targets_are_refused_without_echoing_them(
    web: Api, target: dict[str, Any], reason: str
) -> None:
    created = await create_destination(web, await owner(web), target=target)
    body = error(created, 422, "DESTINATION_TARGET_INVALID")
    assert body["details"] == {"reason": reason}
    for value in target.values():
        assert str(value) not in created.text


@pytest.fixture
async def extra_ports(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    """The web API whose operator added port 8443 to the alert allow-list."""
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, alerts_allowed_ports="443, 8443")
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


async def test_the_setting_extends_the_port_allow_list_at_save_and_on_update(
    extra_ports: Api,
) -> None:
    headers = await owner(extra_ports)
    ok = await create_destination(
        extra_ports, headers, target={"url": "https://hooks.example.com:8443/x"}
    )
    assert ok.status_code == 201, ok.text
    other = await create_destination(
        extra_ports, headers, "second", target={"url": "https://hooks.example.com:9443/x"}
    )
    assert error(other, 422, "DESTINATION_TARGET_INVALID")["details"] == {
        "reason": "port_not_allowed"
    }
    ident = ok.json()["id"]
    moved = await extra_ports.client.patch(
        f"/v1/alerts/destinations/{ident}",
        json={"target": {"url": "https://hooks.example.com:22/x"}},
        headers=headers,
    )
    assert error(moved, 422, "DESTINATION_TARGET_INVALID")["details"] == {
        "reason": "port_not_allowed"
    }


async def test_unknown_fields_and_bad_shapes_are_422s(web: Api) -> None:
    headers = await owner(web)
    for body in (
        {"name": "x", "type": "pagerduty", "target": {"url": HOOK}},
        {"name": "", "type": "webhook", "target": {"url": HOOK}},
        {"name": "x" * 81, "type": "webhook", "target": {"url": HOOK}},
        {"name": "x", "type": "webhook", "target": {"url": HOOK}, "state": "paused"},
        {"name": "x", "type": "webhook", "target": {"url": HOOK, "extra": 1}},
    ):
        response = await web.client.post("/v1/alerts/destinations", json=body, headers=headers)
        assert response.status_code == 422, body


async def test_patch_renames_replaces_the_target_pauses_resumes_and_rotates(web: Api) -> None:
    headers = await owner(web)
    created = (await create_destination(web, headers)).json()
    url = f"/v1/alerts/destinations/{created['id']}"
    async with web.engine.connect() as conn:
        before = (
            await conn.execute(text("SELECT config_encrypted, key_id FROM alert_destinations"))
        ).one()

    renamed = await web.client.patch(url, json={"name": "renamed"}, headers=headers)
    assert renamed.status_code == 200 and renamed.json()["name"] == "renamed"
    assert renamed.json()["signing_secret"] is None

    moved = await web.client.patch(url, json={"target": {"url": SLACK}}, headers=headers)
    assert moved.json()["masked_target"] == "https://hooks.slack.com/…/0000"
    assert SLACK not in moved.text and "SLACKSENTINEL" not in moved.text
    async with web.engine.connect() as conn:
        after = (await conn.execute(text("SELECT config_encrypted FROM alert_destinations"))).one()
    assert bytes(after[0]) != bytes(before[0])

    bad = await web.client.patch(
        url, json={"target": {"url": "http://example.com/x"}}, headers=headers
    )
    assert error(bad, 422, "DESTINATION_TARGET_INVALID")["details"] == {"reason": "not_https"}
    wrong = await web.client.patch(
        url, json={"target": {"recipients": ["a@example.com"]}}, headers=headers
    )
    assert error(wrong, 422, "DESTINATION_TARGET_INVALID")["details"] == {"reason": "target_shape"}

    paused = await web.client.patch(url, json={"state": "paused"}, headers=headers)
    assert (paused.json()["state"], paused.json()["paused_reason"]) == ("paused", "manual")
    await web.client.patch(url, json={"state": "paused"}, headers=headers)  # no second audit row
    resumed = await web.client.patch(url, json={"state": "active"}, headers=headers)
    assert (resumed.json()["state"], resumed.json()["paused_reason"]) == ("active", None)

    rotated = await web.client.patch(url, json={"rotate_signing_secret": True}, headers=headers)
    assert rotated.json()["signing_secret"].startswith("whsec_")
    assert rotated.json()["signing_secret"] != created["signing_secret"]
    again = await web.client.get(url, headers=headers)
    assert "signing_secret" not in again.text

    actions = [a.action for a in await audit(web, "alert.destination.%")]
    assert actions == [
        "alert.destination.create",
        "alert.destination.update",  # rename
        "alert.destination.update",  # target
        "alert.destination.pause",
        "alert.destination.resume",
        "alert.destination.rotate_secret",
    ]
    changed = [a.details.get("changed") for a in await audit(web, "alert.destination.update")]
    assert changed == [["name"], ["target"]]


async def test_a_failing_destination_is_resumed_and_a_new_target_clears_its_failures(
    web: Api,
) -> None:
    headers = await owner(web)
    created = (await create_destination(web, headers)).json()
    dest_id = to_uuid(created["id"])
    async with web.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE alert_destinations SET state = 'failing', "
                "consecutive_failures = 4 WHERE id = :i"
            ),
            {"i": dest_id},
        )
    url = f"/v1/alerts/destinations/{created['id']}"
    moved = (await web.client.patch(url, json={"target": {"url": SLACK}}, headers=headers)).json()
    assert (moved["state"], moved["consecutive_failures"]) == ("active", 0)


async def test_rotating_a_secret_that_does_not_exist_and_taking_a_name_twice_are_refused(
    web: Api,
) -> None:
    headers = await owner(web)
    slack = (
        await create_destination(web, headers, "slack", type="slack", target={"url": SLACK})
    ).json()
    hook = (await create_destination(web, headers, "hook")).json()
    nope = await web.client.patch(
        f"/v1/alerts/destinations/{slack['id']}",
        json={"rotate_signing_secret": True},
        headers=headers,
    )
    assert error(nope, 422, "DESTINATION_INVALID")["details"] == {"reason": "no_signing_secret"}
    clash = await web.client.patch(
        f"/v1/alerts/destinations/{hook['id']}", json={"name": "slack"}, headers=headers
    )
    error(clash, 409, "ALERT_NAME_TAKEN")
    again = await create_destination(web, headers, "slack")
    error(again, 409, "ALERT_NAME_TAKEN")


async def test_deleting_a_destination_closes_pending_alerts_keeps_history_and_mutes_orphan_rules(
    web: Api,
) -> None:
    headers = await owner(web)
    keep = (await create_destination(web, headers, "keep")).json()
    gone = (
        await create_destination(web, headers, "gone", type="slack", target={"url": SLACK})
    ).json()
    both = (await create_rule(web, headers, [keep["id"], gone["id"]], name="both")).json()
    only = (await create_rule(web, headers, [gone["id"]], name="only")).json()
    await post_run(web, "FAILED")
    await web.drain()
    assert len(await rows(web)) == 3  # both -> 2 destinations, only -> 1
    deleted = await web.client.delete(f"/v1/alerts/destinations/{gone['id']}", headers=headers)
    assert deleted.status_code == 204
    found = await rows(web)
    assert len(found) == 3
    by_status = sorted((r.status, r.skip_reason, r.destination_id is None) for r in found)
    assert by_status == [
        ("pending", None, False),
        ("skipped", "destination_deleted", True),
        ("skipped", "destination_deleted", True),
    ]
    rules = {
        r["id"]: r
        for r in (await web.client.get("/v1/alerts/rules", headers=headers)).json()["items"]
    }
    assert rules[both["id"]]["enabled"] and rules[both["id"]]["destination_count"] == 1
    assert not rules[only["id"]]["enabled"] and rules[only["id"]]["destination_count"] == 0
    (entry,) = await audit(web, "alert.destination.delete")
    assert entry.details == {"type": "slack", "rules_disabled": 1}
    assert (
        await web.client.get(f"/v1/alerts/destinations/{gone['id']}", headers=headers)
    ).status_code == 404
    history = (await web.client.get("/v1/alerts/notifications", headers=headers)).json()["items"]
    assert sum(1 for h in history if h["destination"] is None) == 2


async def test_foreign_and_unknown_destination_ids_are_the_same_404(web: Api) -> None:
    headers = await owner(web)
    theirs = await seed_destination(web.engine, web.other.context, name="theirs")
    random = new_id(IdKind.DESTINATION)
    from abb_api.ids import public_id

    seen = []
    for ident in (public_id(IdKind.DESTINATION, theirs), random, "dst_not-an-id"):
        for method, body in (("GET", None), ("PATCH", {"name": "x"}), ("DELETE", None)):
            response = await web.client.request(
                method, f"/v1/alerts/destinations/{ident}", json=body, headers=headers
            )
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "ALERT_DESTINATION_NOT_FOUND"
            seen.append(response.json()["error"]["message"])
    assert len(set(seen)) == 1


# ---------------------------------------------------------------- limits and rate limits


async def test_workspace_limits_return_409(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url,
        web_origin=WEB_ORIGIN,
        alerts_max_destinations_per_workspace=2,
        alerts_max_rules_per_workspace=1,
        alerts_max_destinations_per_rule=1,
    )
    async for api in build_api(database_url, engine, settings=settings):
        headers = await owner(api)
        first = (await create_destination(api, headers, "a")).json()
        second = (await create_destination(api, headers, "b")).json()
        error(await create_destination(api, headers, "c"), 409, "ALERT_LIMIT_REACHED")
        too_many = await create_rule(api, headers, [first["id"], second["id"]])
        error(too_many, 409, "ALERT_LIMIT_REACHED")
        assert (await create_rule(api, headers, [first["id"]], name="r1")).status_code == 201
        error(await create_rule(api, headers, [first["id"]], name="r2"), 409, "ALERT_LIMIT_REACHED")


async def test_alert_changes_are_rate_limited_per_workspace(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, alerts_writes_per_minute=3)
    async for api in build_api(database_url, engine, settings=settings):
        headers = await owner(api)
        codes = [(await create_destination(api, headers, f"d{i}")).status_code for i in range(5)]
        assert codes == [201, 201, 201, 429, 429]
        limited = await create_destination(api, headers, "late")
        body = error(limited, 429, "RATE_LIMITED")
        assert body["retryable"] is True and int(limited.headers["retry-after"]) >= 1
        assert (await api.client.get("/v1/alerts/destinations", headers=headers)).status_code == 200


# ---------------------------------------------------------------- rules


RULE_CASES: list[tuple[str, dict[str, Any]]] = [
    ("approval_requested", {}),
    ("approval_requested", {"agent": "coding-agent"}),
    ("approval_expiring", {"expiring_lead_seconds": 600}),
    ("security_finding", {"min_severity": "high", "agent": "coding-agent"}),
    ("run_failed", {"run_statuses": ["TIMED_OUT", "FAILED"]}),
    ("spend_over", {"threshold_usd": 12.5}),
]


@pytest.mark.parametrize(("trigger", "filters"), RULE_CASES)
async def test_a_valid_rule_is_stored_with_normalised_filters(
    web: Api, trigger: str, filters: dict[str, Any]
) -> None:
    headers = await owner(web)
    # The agent filter needs an agent that exists: ingest a run for it first.
    await post_run(web, "SUCCESS")
    dest = (await create_destination(web, headers)).json()
    created = await create_rule(
        web, headers, [dest["id"]], trigger=trigger, filters=filters,
        project_id=web.tenant.projects["alpha"], cooldown_seconds=120,
    )  # fmt: skip
    assert created.status_code == 201, created.text
    body = created.json()
    assert (
        body["trigger"] == trigger and body["enabled"] is True and body["cooldown_seconds"] == 120
    )
    assert body["project_id"] == web.tenant.projects["alpha"]
    assert body["destination_count"] == 1 and body["destinations"][0]["id"] == dest["id"]
    stored = {k: v for k, v in body["filters"].items() if v is not None}
    expected = dict(filters)
    if trigger == "run_failed":
        expected["run_statuses"] = ["FAILED", "TIMED_OUT"]
    if trigger == "spend_over":
        expected["period"] = "day"
    assert stored == expected
    (entry,) = await audit(web, "alert.rule.create")
    assert entry.details == {"trigger": trigger, "destinations": 1}
    assert HOOK not in json.dumps(entry.details)


async def test_filters_that_do_not_fit_the_trigger_are_refused_by_field_name(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    na, req = "not_applicable", "required"
    cases: list[tuple[str, dict[str, Any], str, str]] = [
        ("approval_requested", {"min_severity": "high"}, "min_severity", na),
        ("approval_requested", {"threshold_usd": 5}, "threshold_usd", na),
        ("approval_expiring", {}, "expiring_lead_seconds", req),
        (
            "approval_expiring",
            {"expiring_lead_seconds": 600, "run_statuses": ["FAILED"]},
            "run_statuses",
            na,
        ),
        ("security_finding", {}, "min_severity", req),
        (
            "security_finding",
            {"min_severity": "high", "expiring_lead_seconds": 60},
            "expiring_lead_seconds",
            na,
        ),
        ("run_failed", {"min_severity": "high"}, "min_severity", na),
        ("spend_over", {}, "threshold_usd", req),
        ("spend_over", {"threshold_usd": 1, "run_statuses": ["FAILED"]}, "run_statuses", na),
        ("run_failed", {"agent": "nobody-here"}, "agent", "unknown_agent"),
    ]
    for trigger, filters, field, reason in cases:
        response = await create_rule(web, headers, [dest], trigger=trigger, filters=filters)
        body = error(response, 422, "RULE_INVALID")
        assert body["details"] == {"field": field, "reason": reason}, (trigger, filters)


async def test_out_of_range_values_and_unknown_vocabulary_are_422s(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    bad: list[dict[str, Any]] = [
        {"trigger": "approval_expiring", "filters": {"expiring_lead_seconds": 59}},
        {"trigger": "approval_expiring", "filters": {"expiring_lead_seconds": 86401}},
        {"trigger": "spend_over", "filters": {"threshold_usd": 0}},
        {"trigger": "spend_over", "filters": {"threshold_usd": -3}},
        {"trigger": "spend_over", "filters": {"threshold_usd": 1_000_001}},
        {"trigger": "spend_over", "filters": {"threshold_usd": 5, "period": "week"}},
        {"trigger": "security_finding", "filters": {"min_severity": "urgent"}},
        {"trigger": "run_failed", "filters": {"run_statuses": ["SUCCESS"]}},
        {"trigger": "nonsense"},
        {"cooldown_seconds": -1},
        {"cooldown_seconds": 604801},
        {"destination_ids": []},
        {"destination_ids": ["dst_bad"]},
        {"name": ""},
        {"filters": {"surprise": 1}},
    ]
    for extra in bad:
        ids = extra.pop("destination_ids", [dest])
        response = await create_rule(web, headers, ids, **extra)
        assert response.status_code == 422, extra
    accepted = await create_rule(
        web, headers, [dest], trigger="approval_expiring", filters={"expiring_lead_seconds": 60},
        cooldown_seconds=604800, name="edges",
    )  # fmt: skip
    assert accepted.status_code == 201


async def test_a_rule_cannot_name_another_workspaces_project_or_destination(web: Api) -> None:
    headers = await owner(web)
    mine = (await create_destination(web, headers)).json()["id"]
    from abb_api.ids import public_id

    theirs = await seed_destination(web.engine, web.other.context, name="theirs")
    foreign_dest = public_id(IdKind.DESTINATION, theirs)
    random_dest = new_id(IdKind.DESTINATION)
    responses = [
        await create_rule(web, headers, [foreign_dest], name="a"),
        await create_rule(web, headers, [random_dest], name="b"),
        await create_rule(web, headers, [mine, foreign_dest], name="c"),
    ]
    assert {r.status_code for r in responses} == {404}
    assert {r.json()["error"]["code"] for r in responses} == {"ALERT_DESTINATION_NOT_FOUND"}
    foreign_project = web.other.projects["p"]
    for ident in (foreign_project, new_id(IdKind.PROJECT)):
        response = await create_rule(web, headers, [mine], name="p", project_id=ident)
        assert error(response, 404, "PROJECT_NOT_FOUND")
    assert await rows(web) == []


async def test_names_are_unique_per_workspace_and_patch_edits_mutes_and_replaces(web: Api) -> None:
    headers = await owner(web)
    one = (await create_destination(web, headers, "one")).json()["id"]
    two = (
        await create_destination(web, headers, "two", type="slack", target={"url": SLACK})
    ).json()["id"]
    rule = (await create_rule(web, headers, [one])).json()
    error(await create_rule(web, headers, [one]), 409, "ALERT_NAME_TAKEN")
    url = f"/v1/alerts/rules/{rule['id']}"
    muted = await web.client.patch(url, json={"enabled": False}, headers=headers)
    assert muted.json()["enabled"] is False and muted.json()["name"] == "failures"
    edited = await web.client.patch(
        url,
        json={
            "name": "renamed",
            "destination_ids": [one, two],
            "filters": {"run_statuses": ["BLOCKED"]},
            "cooldown_seconds": 90,
            "project_id": web.tenant.projects["beta"],
        },
        headers=headers,
    )
    body = edited.json()
    assert (body["name"], body["cooldown_seconds"], body["destination_count"]) == ("renamed", 90, 2)
    assert body["filters"]["run_statuses"] == ["BLOCKED"] and body["enabled"] is False
    assert body["project_id"] == web.tenant.projects["beta"]
    cleared = await web.client.patch(url, json={"project_id": None}, headers=headers)
    assert cleared.json()["project_id"] is None
    wrong = await web.client.patch(url, json={"filters": {"threshold_usd": 5}}, headers=headers)
    assert error(wrong, 422, "RULE_INVALID")["details"]["field"] == "threshold_usd"
    retrigger = await web.client.patch(url, json={"trigger": "spend_over"}, headers=headers)
    assert retrigger.status_code == 422  # the trigger cannot change
    unchanged = await web.client.patch(url, json={}, headers=headers)
    assert unchanged.status_code == 200
    updates = await audit(web, "alert.rule.update")
    assert [u.details["changed"] for u in updates] == [
        ["enabled"],
        ["project_id", "filters", "destination_ids", "name", "cooldown_seconds"],
        ["project_id"],
    ]


async def test_deleting_a_rule_closes_its_pending_alerts_and_keeps_the_history(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    rule = (await create_rule(web, headers, [dest])).json()
    await post_run(web, "FAILED")
    await web.drain()
    assert [r.status for r in await rows(web)] == ["pending"]
    assert (
        await web.client.delete(f"/v1/alerts/rules/{rule['id']}", headers=headers)
    ).status_code == 204
    (row,) = await rows(web)
    assert (row.status, row.skip_reason, row.rule_id) == ("skipped", "rule_deleted", None)
    assert (
        await web.client.get(f"/v1/alerts/rules/{rule['id']}", headers=headers)
    ).status_code == 404
    (entry,) = await audit(web, "alert.rule.delete")
    assert entry.details == {"trigger": "run_failed"}
    history = (await web.client.get("/v1/alerts/notifications", headers=headers)).json()["items"]
    assert history[0]["rule"] is None


async def test_scheduled_rules_start_the_sweep_timer_and_it_stops_when_none_is_left(
    web: Api,
) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    await create_rule(web, headers, [dest], name="event rule")

    async def sweep_jobs(status: str) -> int:
        async with web.engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM outbox_jobs WHERE job_type = :t AND status = :s"
                        ),
                        {"t": ALERT_SWEEP, "s": status},
                    )
                ).scalar_one()
            )

    assert await sweep_jobs("pending") == 0  # an event rule needs no timer
    spend = (
        await create_rule(
            web, headers, [dest], name="spend", trigger="spend_over", filters={"threshold_usd": 5}
        )
    ).json()
    assert await sweep_jobs("pending") == 1
    await web.drain()  # runs the sweep, which arms the next one
    assert await sweep_jobs("done") == 1 and await sweep_jobs("pending") == 1
    await web.client.patch(
        f"/v1/alerts/rules/{spend['id']}", json={"enabled": False}, headers=headers
    )
    async with web.engine.begin() as conn:
        await conn.execute(
            text("UPDATE outbox_jobs SET available_at = now() WHERE status = 'pending'")
        )
    await web.drain()
    assert await sweep_jobs("pending") == 0  # nothing scheduled is left: the chain ended
    await web.client.patch(
        f"/v1/alerts/rules/{spend['id']}", json={"enabled": True}, headers=headers
    )
    assert await sweep_jobs("pending") == 1  # switching it back on starts it again


# ---------------------------------------------------------------- roles


async def test_destinations_are_for_owners_and_admins_and_rules_are_read_by_the_rest(
    web: Api,
) -> None:
    owner_headers = await owner(web)
    dest = (await create_destination(web, owner_headers)).json()
    rule = (await create_rule(web, owner_headers, [dest["id"]])).json()
    await post_run(web, "FAILED")
    await web.drain()
    for email, role in (
        ("dev@acme.test", "DEVELOPER"),
        ("view@acme.test", "VIEWER"),
        ("sec@acme.test", "SECURITY"),
    ):
        headers = await person(web, email, role)
        listed = await web.client.get("/v1/alerts/rules", headers=headers)
        assert listed.status_code == 200, role
        (item,) = listed.json()["items"]
        assert item["destination_count"] == 1 and item["destinations"] == []
        assert dest["name"] not in listed.text and dest["id"] not in listed.text
        got = await web.client.get(f"/v1/alerts/rules/{rule['id']}", headers=headers)
        assert got.status_code == 200 and got.json()["destinations"] == []
        history = (await web.client.get("/v1/alerts/notifications", headers=headers)).json()[
            "items"
        ]
        (entry,) = history
        assert entry["destination"] == {"id": None, "name": None, "type": "webhook"}
        assert dest["id"] not in json.dumps(history)
        for method, path, body in (
            ("GET", "/v1/alerts/destinations", None),
            ("GET", f"/v1/alerts/destinations/{dest['id']}", None),
            (
                "POST",
                "/v1/alerts/destinations",
                {"name": "n", "type": "slack", "target": {"url": SLACK}},
            ),
            ("PATCH", f"/v1/alerts/rules/{rule['id']}", {"enabled": False}),
            ("DELETE", f"/v1/alerts/rules/{rule['id']}", None),
        ):
            denied = await web.client.request(method, path, json=body, headers=headers)
            assert denied.status_code == 403, (role, method, path)
            assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
        filtered = await web.client.get(
            "/v1/alerts/notifications", params={"destination_id": dest["id"]}, headers=headers
        )
        assert filtered.status_code == 403 and filtered.json()["error"]["details"] == {
            "required_permission": "alert.destination.read"
        }
    billing = await person(web, "bill@acme.test", "BILLING")
    assert (await web.client.get("/v1/alerts/rules", headers=billing)).status_code == 403
    assert (await web.client.get("/v1/alerts/notifications", headers=billing)).status_code == 403
    admin = await person(web, "admin@acme.test", "ADMIN")
    assert (await web.client.get("/v1/alerts/destinations", headers=admin)).status_code == 200
    seen = (await web.client.get("/v1/alerts/rules", headers=admin)).json()["items"][0]
    assert seen["destinations"][0]["id"] == dest["id"]


async def test_api_keys_cannot_touch_alerting_at_all(web: Api) -> None:
    for token in ("writer", "wide_reader", "wide"):
        for method, path in (
            ("GET", "/v1/alerts/rules"),
            ("GET", "/v1/alerts/destinations"),
            ("GET", "/v1/alerts/notifications"),
            ("POST", "/v1/alerts/rules"),
        ):
            response = await web.client.request(method, path, json={}, headers=web.headers(token))
            assert response.status_code == 403, (token, method, path)
            assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_the_approval_id_is_withheld_from_roles_that_may_not_read_approvals(web: Api) -> None:
    await publish(web)
    owner_headers = await owner(web)
    dest = (await create_destination(web, owner_headers)).json()["id"]
    await create_rule(web, owner_headers, [dest], trigger="approval_requested")
    _, approval_id = await ask(web)
    await web.drain()
    viewer = await person(web, "v@acme.test", "VIEWER")
    developer = await person(web, "d@acme.test", "DEVELOPER")
    seen = {}
    for name, headers in (("owner", owner_headers), ("developer", developer), ("viewer", viewer)):
        (item,) = (await web.client.get("/v1/alerts/notifications", headers=headers)).json()[
            "items"
        ]
        seen[name] = item["approval_id"]
    assert seen == {"owner": approval_id, "developer": approval_id, "viewer": None}


# ---------------------------------------------------------------- history


async def test_history_lists_filters_and_pages_newest_first(web: Api) -> None:
    headers = await owner(web)
    one = (await create_destination(web, headers, "one")).json()
    two = (
        await create_destination(web, headers, "two", type="slack", target={"url": SLACK})
    ).json()
    rule = (await create_rule(web, headers, [one["id"], two["id"]])).json()
    for _ in range(3):
        await post_run(web, "FAILED")
    await post_run(web, "SUCCESS")
    await web.drain()
    first = await web.client.get("/v1/alerts/notifications", params={"limit": 4}, headers=headers)
    page = first.json()
    assert len(page["items"]) == 4 and page["next_cursor"]
    second = await web.client.get(
        "/v1/alerts/notifications",
        params={"limit": 4, "cursor": page["next_cursor"]},
        headers=headers,
    )
    rest = second.json()
    assert len(rest["items"]) == 2 and rest["next_cursor"] is None
    ids = [i["id"] for i in page["items"] + rest["items"]]
    assert len(set(ids)) == 6 and all(i.startswith("alt_") for i in ids)
    item = page["items"][0]
    assert item["rule"] == {"id": rule["id"], "name": "failures"}
    assert item["destination"]["id"] in (one["id"], two["id"])
    assert (item["trigger"], item["status"], item["attempts"], item["error_code"]) == (
        "run_failed", "pending", 0, None,
    )  # fmt: skip
    assert item["run_id"].startswith("run_") and item["approval_id"] is None
    assert set(item) == {
        "id", "rule", "destination", "trigger", "severity", "status", "skip_reason", "attempts",
        "error_code", "project_id", "run_id", "finding_id", "approval_id", "agent_slug",
        "created_at", "delivered_at", "next_attempt_at",
    }  # fmt: skip
    by_destination = await web.client.get(
        "/v1/alerts/notifications", params={"destination_id": two["id"]}, headers=headers
    )
    assert len(by_destination.json()["items"]) == 3
    by_status = await web.client.get(
        "/v1/alerts/notifications", params={"status": "delivered"}, headers=headers
    )
    assert by_status.json()["items"] == []
    by_rule = await web.client.get(
        "/v1/alerts/notifications",
        params={"rule_id": rule["id"], "trigger": "run_failed"},
        headers=headers,
    )
    assert len(by_rule.json()["items"]) == 6
    other_trigger = await web.client.get(
        "/v1/alerts/notifications", params={"trigger": "spend_over"}, headers=headers
    )
    assert other_trigger.json()["items"] == []
    until = await web.client.get(
        "/v1/alerts/notifications", params={"until": "2000-01-01T00:00:00Z"}, headers=headers
    )
    assert until.json()["items"] == []
    naive = await web.client.get(
        "/v1/alerts/notifications", params={"since": "2026-01-01T00:00:00"}, headers=headers
    )
    error(naive, 422, "INVALID_TIME")
    bad_cursor = await web.client.get(
        "/v1/alerts/notifications", params={"cursor": "garbage"}, headers=headers
    )
    error(bad_cursor, 400, "CURSOR_INVALID")


async def test_history_never_shows_another_workspaces_rows(web: Api) -> None:
    headers = await owner(web)
    mine = await seed_destination(web.engine, web.tenant.context, name="mine")
    await seed_rule(web.engine, web.tenant.context, [mine], name="mine")
    theirs = await seed_destination(web.engine, web.other.context, name="theirs")
    await seed_rule(web.engine, web.other.context, [theirs], name="theirs")
    await post_run(web, "FAILED")
    await post_run(web, "FAILED", token="other")
    await web.drain()
    items = (await web.client.get("/v1/alerts/notifications", headers=headers)).json()["items"]
    assert len(items) == 1 and items[0]["rule"]["name"] == "mine"
    assert web.other.projects["p"] not in json.dumps(items)


# ---------------------------------------------------------------- secrets, everywhere


async def test_no_secret_appears_in_any_response_audit_row_or_log(
    web: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    headers = await owner(web)
    texts: list[str] = []
    created = await create_destination(web, headers, "hook")
    secret = created.json()["signing_secret"]
    texts.append(created.text.replace(secret, "<signing secret, shown once>"))
    slack = await create_destination(web, headers, "slack", type="slack", target={"url": SLACK})
    texts.append(slack.text)
    hook_id, slack_id = created.json()["id"], slack.json()["id"]
    rule = await create_rule(web, headers, [hook_id, slack_id])
    texts.append(rule.text)
    patched = await web.client.patch(
        f"/v1/alerts/destinations/{hook_id}",
        json={"target": {"url": SLACK}, "state": "paused"},
        headers=headers,
    )
    texts.append(patched.text)
    rotated = await web.client.patch(
        f"/v1/alerts/destinations/{hook_id}", json={"rotate_signing_secret": True}, headers=headers
    )
    new_secret = rotated.json()["signing_secret"]
    texts.append(rotated.text.replace(new_secret, "<signing secret, shown once>"))
    for path in ("/v1/alerts/destinations", "/v1/alerts/rules", "/v1/alerts/notifications"):
        texts.append((await web.client.get(path, headers=headers)).text)
    bad = await create_destination(
        web, headers, "bad", target={"url": "http://secret-host.example/SENTINELSECRETTOKEN"}
    )
    texts.append(bad.text)
    await web.client.delete(f"/v1/alerts/destinations/{slack_id}", headers=headers)
    for blob in texts:
        for needle in (*PLAIN, secret, new_secret, "SENTINELSECRETTOKEN1234", "secret-host"):
            assert needle not in blob, needle
    entries = await audit(web)
    dump = json.dumps([[e.action, e.resource_id, e.details] for e in entries])
    for needle in (*PLAIN, secret, new_secret, "hooks.example.com", "slack.com"):
        assert needle not in dump, needle
    for needle in (*PLAIN, secret, new_secret, "secret-host"):
        assert needle not in caplog.text, needle
    async with web.engine.connect() as conn:
        all_rows = (await conn.execute(text("SELECT t::text FROM alert_destinations t"))).all()
    assert all_rows and all(
        secret not in r[0] and "SENTINELSECRETTOKEN" not in r[0] for r in all_rows
    )


# ---------------------------------------------------------------- preview


async def preview(api: Api, headers: dict[str, str], rule_id: str, days: int = 7) -> dict[str, Any]:
    response = await api.client.get(
        f"/v1/alerts/rules/{rule_id}/preview", params={"days": days}, headers=headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_the_preview_counts_failed_runs_by_status_project_and_agent(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    for kind in ("FAILED", "FAILED", "TIMED_OUT", "BLOCKED", "SUCCESS"):
        await post_run(web, kind)
    await post_run(web, "FAILED", agent="planner")
    await post_run(web, "FAILED", token="beta")
    await post_run(web, "FAILED", token="other")  # another workspace: never counted
    await web.drain()
    web.clock.now = NOW + timedelta(minutes=1)
    everything = (await create_rule(web, headers, [dest], name="all")).json()
    # 2 failed + 1 timed out + 1 blocked (coding-agent), 1 planner and 1 beta; never globex's.
    assert (await preview(web, headers, everything["id"]))["count"] == 6
    only_failed = (
        await create_rule(web, headers, [dest], name="f", filters={"run_statuses": ["FAILED"]})
    ).json()
    result = await preview(web, headers, only_failed["id"])
    assert result["count"] == 4
    assert result["daily"] == [{"day": "2026-10-07", "count": 4}]
    alpha_planner = (
        await create_rule(
            web, headers, [dest], name="ap", project_id=web.tenant.projects["alpha"],
            filters={"agent": "planner"},
        )
    ).json()  # fmt: skip
    assert (await preview(web, headers, alpha_planner["id"]))["count"] == 1
    beta = (
        await create_rule(web, headers, [dest], name="b", project_id=web.tenant.projects["beta"])
    ).json()
    assert (await preview(web, headers, beta["id"]))["count"] == 1
    # The window: a day back from 12:06 on the 7th still holds runs that ended just after noon.
    assert (await preview(web, headers, only_failed["id"], days=1))["count"] == 4
    web.clock.now = NOW + timedelta(days=3)
    headers = await member(web, "owner@acme.test", "OWNER")  # the old session has gone idle
    assert (await preview(web, headers, only_failed["id"], days=1))["count"] == 0
    assert (await preview(web, headers, only_failed["id"], days=30))["count"] == 4
    assert await rows(web) == []  # a preview delivers and stores nothing


async def test_the_preview_counts_findings_at_or_above_the_severity(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    run, _ = planted_run("acme")
    assert (await web.post_batch(run.wires)).status_code == 202
    await web.drain()
    async with web.engine.connect() as conn:
        stored = [
            r.severity
            for r in await conn.execute(
                text("SELECT severity FROM findings WHERE detector LIKE 'sec.%'")
            )
        ]
    ranks = m.SEVERITY_RANK
    for floor in ("info", "high", "critical"):
        rule = (
            await create_rule(
                web,
                headers,
                [dest],
                name=floor,
                trigger="security_finding",
                filters={"min_severity": floor},
            )
        ).json()
        expected = sum(1 for s in stored if ranks[s] >= ranks[floor])
        assert (await preview(web, headers, rule["id"]))["count"] == expected
    critical = sum(1 for s in stored if s == "critical")
    assert 0 < critical < len(stored)


async def test_the_preview_counts_approvals_requested_and_about_to_expire(web: Api) -> None:
    await publish(web)
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    await ask(web)
    await ask(web, arguments={"remote": "origin", "force": True})
    requested = (
        await create_rule(web, headers, [dest], name="r", trigger="approval_requested")
    ).json()
    expiring = (
        await create_rule(
            web, headers, [dest], name="e", trigger="approval_expiring",
            filters={"expiring_lead_seconds": 600},
        )
    ).json()  # fmt: skip
    assert (await preview(web, headers, requested["id"]))["count"] == 2
    # 15-minute approvals asked at NOW reach their 10-minute lead at NOW + 5 minutes.
    web.clock.now = NOW + timedelta(minutes=4)
    assert (await preview(web, headers, expiring["id"]))["count"] == 0
    web.clock.now = NOW + timedelta(minutes=6)
    assert (await preview(web, headers, expiring["id"]))["count"] == 2
    async with (
        web.engine.begin() as conn
    ):  # one was withdrawn before its lead: it would not have fired
        await conn.execute(
            text(
                "UPDATE approvals SET state = 'CANCELLED', closed_at = :c WHERE id = "
                "(SELECT id FROM approvals ORDER BY id LIMIT 1)"
            ),
            {"c": NOW + timedelta(minutes=1)},
        )
    assert (await preview(web, headers, expiring["id"]))["count"] == 1
    assert (await preview(web, headers, requested["id"]))["count"] == 2


async def test_the_preview_counts_days_over_the_spend_threshold(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    await post_run(web, "SUCCESS", cost=7.0)
    await post_run(web, "FAILED", cost=5.5)
    await post_run(web, "SUCCESS", cost=100.0, agent="planner")
    await post_run(web, "SUCCESS", cost=999.0, token="other")
    await web.drain()
    cases = [
        (10, None, 1),
        (112.5, None, 0),
        (112.49, None, 1),
        (10, "coding-agent", 1),
        (13, "coding-agent", 0),
    ]
    for n, (threshold, agent, expected) in enumerate(cases):
        filters: dict[str, Any] = {"threshold_usd": threshold}
        if agent:
            filters["agent"] = agent
        rule = (
            await create_rule(
                web, headers, [dest], name=f"s{n}", trigger="spend_over", filters=filters
            )
        ).json()
        result = await preview(web, headers, rule["id"])
        assert result["count"] == expected, (threshold, agent)
        if expected:
            assert result["daily"] == [{"day": "2026-10-07", "count": 1}]


async def test_preview_input_and_ids_are_checked(web: Api) -> None:
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    rule = (await create_rule(web, headers, [dest])).json()
    for days in (0, 31, -1):
        response = await web.client.get(
            f"/v1/alerts/rules/{rule['id']}/preview", params={"days": days}, headers=headers
        )
        assert response.status_code == 422
    for ident in (new_id(IdKind.ALERT_RULE), "rul_bad"):
        response = await web.client.get(f"/v1/alerts/rules/{ident}/preview", headers=headers)
        assert error(response, 404, "ALERT_RULE_NOT_FOUND")


async def test_the_scheduler_chain_survives_a_check_that_fails(
    web: Api, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken scheduled check is logged by class and the timer keeps going (ADR-150)."""
    headers = await owner(web)
    dest = (await create_destination(web, headers)).json()["id"]
    await create_rule(web, headers, [dest], trigger="spend_over", filters={"threshold_usd": 1})
    async with web.engine.begin() as conn:  # make the spend query fail at the database
        await conn.execute(text("ALTER TABLE cost_calculations RENAME TO cost_calculations_x"))
    try:
        await web.drain()
    finally:
        async with web.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE cost_calculations_x RENAME TO cost_calculations"))
    async with web.engine.connect() as conn:
        states = {
            r.status: r.n
            for r in await conn.execute(
                text(
                    "SELECT status, count(*) AS n FROM outbox_jobs "
                    "WHERE job_type = :t GROUP BY status"
                ),
                {"t": ALERT_SWEEP},
            )
        }
    assert states == {"done": 1, "pending": 1}
    assert "alert check failed" in caplog.text
