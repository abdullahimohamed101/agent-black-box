"""Captured content in an intent: scrubbed before storage, never in a response, event, audit row or
log, and the hash never leaves the server (spec §90, INV-5, gate 6)."""

import logging
from typing import Any

import pytest

from abb_api.security.redaction import ServerRedactor
from abb_api.security.scanner import ScanFailed
from tests.api_fixtures import Api
from tests.approvals.helpers import (
    SENTINEL,
    approval_row,
    approve,
    ask,
    consume,
    decide,
    decision,
    events_of,
    intent,
    member,
    publish,
)
from tests.test_audit import rows

TOKEN = "ghp_" + "c" * 36
AWS = "AKIA" + "Z" * 16


async def test_secrets_in_the_intent_are_scrubbed_before_storage_and_in_the_detail(
    api: Api,
) -> None:
    await publish(api)
    body = intent(
        resource=f"https://x:{TOKEN}@host/main",
        arguments={
            "command": f"git push https://u:{TOKEN}@host/repo",
            "password": "hunter2hunter2",
            "nested": {"aws": AWS, "items": [f"Bearer {'t' * 30}"]},
        },
        capability="github.push",
    )
    body["resource"] = "main"  # keep the gated resource; put the secret in the arguments only
    out = (await decide(api, body)).json()
    row = await approval_row(api, out["approval_id"])
    stored = f"{row['arguments_scrubbed']} {row['resource_scrubbed']}"
    for secret in (TOKEN, "hunter2hunter2", AWS, "t" * 30):
        assert secret not in stored
    assert row["scrub_count"] >= 3 and row["scrub_dropped"] is False
    owner = await member(api, "owner@acme.test", "OWNER")
    detail = (await api.client.get(f"/v1/approvals/{out['approval_id']}", headers=owner)).json()
    assert TOKEN not in str(detail) and "hunter2hunter2" not in str(detail)
    assert detail["redactions"] == row["scrub_count"] and detail["content_withheld"] is False
    assert detail["arguments"]["password"].startswith("[REDACTED")


async def test_a_scan_failure_drops_the_content_instead_of_storing_it_raw(api: Api) -> None:
    class Exploding:
        def scan_text(self, *args: Any, **kwargs: Any) -> Any:
            raise ScanFailed("boom")

    await publish(api)
    api.app.state.approvals._redactor = ServerRedactor(scanner=Exploding())  # type: ignore[arg-type]
    body = intent(arguments={"note": SENTINEL, "token": TOKEN})
    out = (await decide(api, body)).json()
    row = await approval_row(api, out["approval_id"])
    assert row["arguments_scrubbed"] is None and row["scrub_dropped"] is True
    assert row["resource_scrubbed"] is None  # "main" could not be cleared either
    owner = await member(api, "owner@acme.test", "OWNER")
    detail = (await api.client.get(f"/v1/approvals/{out['approval_id']}", headers=owner)).json()
    assert detail["content_withheld"] is True and detail["arguments"] is None
    assert SENTINEL not in str(detail) and TOKEN not in str(detail)
    # Review P2-4: what could not be shown cannot be approved (fail closed); denying still works.
    refused = await approve(api, out["approval_id"])
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "APPROVAL_CONTENT_UNAVAILABLE"
    assert (await approval_row(api, out["approval_id"]))["state"] == "REQUESTED"
    assert (await decision(api, owner, out["approval_id"], "deny")).status_code == 200


async def test_a_planted_sentinel_appears_in_no_response_event_audit_row_or_log(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(api)
    body = intent(
        resource="main",
        arguments={"note": SENTINEL, "list": [SENTINEL], "nested": {SENTINEL: SENTINEL}},
        idempotency_key="k1",
    )
    responses = [await decide(api, body)]
    approval_id = responses[0].json()["approval_id"]
    owner = await member(api, "owner@acme.test", "OWNER")
    responses.append(
        await api.client.get(
            f"/v1/approvals/{approval_id}/wait?timeout=0", headers=api.headers("writer")
        )
    )
    responses.append(await consume(api, approval_id, {**body, "resource": "other"}))  # refused
    await api.client.post(
        f"/v1/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=owner
    )
    responses.append(await consume(api, approval_id, body))
    responses.append(await consume(api, approval_id, body))  # replay
    for response in responses:
        assert SENTINEL not in response.text
    visible = [str(e.attributes) + str(e.sdk) for e in await events_of(api, body["run_id"])]
    visible += [str(r.details) + str(r.resource_id) for r in await rows(api)]
    assert SENTINEL not in " ".join(visible)
    assert SENTINEL not in caplog.text and SENTINEL not in " ".join(
        str(r.__dict__) for r in caplog.records
    )
    # The arguments are in exactly one place: the approver's detail view (and the DB row behind it).
    detail = (await api.client.get(f"/v1/approvals/{approval_id}", headers=owner)).text
    assert SENTINEL in detail


async def test_the_intent_hash_is_never_returned_or_logged(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(api)
    body, approval_id = await ask(api)
    row = await approval_row(api, approval_id)
    digest = row["intent_hash"]
    assert len(digest) == 64
    owner = await member(api, "owner@acme.test", "OWNER")
    await api.client.post(
        f"/v1/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=owner
    )
    texts = [
        (await decide(api, body)).text,
        (await api.client.get(f"/v1/approvals/{approval_id}", headers=owner)).text,
        (await api.client.get("/v1/approvals?state=all", headers=owner)).text,
        (
            await api.client.get(
                f"/v1/approvals/{approval_id}/wait?timeout=0", headers=api.headers("writer")
            )
        ).text,
        (await consume(api, approval_id, {**body, "resource": "x"})).text,
        (await consume(api, approval_id, body)).text,
        (await consume(api, approval_id, body)).text,
        caplog.text,
    ]
    texts += [str(e.attributes) for e in await events_of(api, body["run_id"])]
    texts += [str(r.details) for r in await rows(api)]
    assert not [t for t in texts if digest in t]
    assert "intent_hash" not in " ".join(texts)
