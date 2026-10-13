"""Server-side redaction and capture modes through the real API and database (ADR-130).

Every test sends what an SDK that skips redaction would send, and asserts on what is *stored*: the
rows in `events`, the artifact files on disk, and a scan of every table for the planted secrets.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.security.scanner import ScanFailed, ScanTooLarge, SecretScanner
from abb_api.security.settings import Actor, CaptureMode, SecuritySettingsService
from tests.api_fixtures import Api, build_api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.secret_scan_helpers import (
    AWS_SENTINEL,
    GITHUB_SENTINEL,
    KEY_BODY_SENTINEL,
    PRIVATE_KEY_SENTINEL,
    find_in_database,
    find_on_disk,
)

SECRETS = (AWS_SENTINEL, GITHUB_SENTINEL, KEY_BODY_SENTINEL)
OWNER = Actor("user", "user:usr_test")
ARTIFACT = "artifact://art_01J90000000000000000000031"


@pytest.fixture
async def api_store(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[Api]:
    settings = make_settings(database_url, artifact_dir=str(tmp_path / "store"))
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


async def set_mode(api: Api, mode: CaptureMode, project: str = "alpha") -> None:
    await SecuritySettingsService(api.engine).update(
        api.tenant.context, api.tenant.project_uuids[project], OWNER, capture_mode=mode
    )


def sdk_event(n: int, run: dict[str, str] | None = None, **fields: Any) -> dict[str, Any]:
    """An event as an SDK with redaction switched off would send it, secrets and all."""
    attributes = {
        "tool.name": "shell",
        "shell.command": f"curl -H 'x-key: {AWS_SENTINEL}' https://api.example.com/v1",
        "shell.stdout_artifact": ARTIFACT,
        "diff.artifact": ARTIFACT,
        "error.message": f"failed with token {GITHUB_SENTINEL}",
        "metadata.owner": "alice@example.com",
    }
    event = wire_event(
        run or make_run_ids(),
        n,
        event_type="tool.call.completed",
        attributes=attributes,
        payload={
            "output": f"found {AWS_SENTINEL} in env",
            "key": PRIVATE_KEY_SENTINEL,
            "password": "hunter2",
            "nested": {"items": ["a", f"see {GITHUB_SENTINEL}"]},
            "count": 3,
        },
        payload_ref=ARTIFACT,
        tags=[f"t-{AWS_SENTINEL}", "ok"],
    )
    event.update(fields)
    return event


async def stored(api: Api, event_id: str) -> Any:
    async with api.engine.connect() as conn:
        row = (
            await conn.execute(select(t.events).where(t.events.c.event_id == uuid_of(event_id)))
        ).first()
    assert row is not None
    return row


def uuid_of(public: str) -> Any:
    from abb_event_schema.ids import to_uuid

    return to_uuid(public)


# ------------------------------------------------------------------------- FULL


async def test_full_redacts_every_secret_and_records_what_it_did(api: Api) -> None:
    event = sdk_event(1)
    response = await api.post_batch([event])
    assert response.status_code == 202 and response.json()["accepted"] == 1
    row = await stored(api, event["event_id"])
    attributes = row.attributes
    assert attributes["shell.command"] == (
        "curl -H 'x-key: [REDACTED:aws_access_key]' https://api.example.com/v1"
    )
    assert attributes["error.message"] == "failed with token [REDACTED:github_token]"
    assert attributes["redaction.server_count"] == 7
    assert attributes["redaction.server_kinds"] == [
        "aws_access_key", "denied_key", "github_token", "private_key",
    ]  # fmt: skip
    assert "redaction.dropped_reason" not in attributes
    assert row.payload["output"] == "found [REDACTED:aws_access_key] in env"
    assert row.payload["key"] == "[REDACTED:private_key]"
    assert row.payload["password"] == "[REDACTED:password]"
    assert row.payload["nested"] == {"items": ["a", "see [REDACTED:github_token]"]}
    assert row.payload["count"] == 3
    assert row.tags == ["t-[REDACTED:aws_access_key]", "ok"]
    assert row.payload_ref == ARTIFACT  # FULL keeps references; the artifact is scanned itself
    assert attributes["shell.stdout_artifact"] == ARTIFACT
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_a_clean_event_is_stored_exactly_as_sent(api: Api) -> None:
    event = wire_event(
        make_run_ids(), 1, attributes={"tool.name": "search", "tool.result_count": 3}
    )
    assert (await api.post_batch([event])).json()["accepted"] == 1
    row = await stored(api, event["event_id"])
    assert row.attributes == {"tool.name": "search", "tool.result_count": 3}
    assert row.tags == [] and row.payload is None


async def test_text_the_sdk_already_redacted_is_not_a_server_finding(api: Api) -> None:
    event = wire_event(
        make_run_ids(),
        1,
        attributes={
            "tool.name": "x",
            "shell.command": (
                "curl -H 'Authorization: Bearer [REDACTED:bearer]' -u [REDACTED:cli_user] "
                "https://[REDACTED:url_credentials]@h/?token=[REDACTED:url_param] "
                "PASSWORD=[REDACTED:credential]"
            ),
        },
        payload={"password": "[REDACTED:password]", "text": "key=[REDACTED:credential]"},
    )
    await api.post_batch([event])
    row = await stored(api, event["event_id"])
    assert "redaction.server_count" not in row.attributes
    assert row.attributes["shell.command"] == event["attributes"]["shell.command"]
    assert row.payload == event["payload"]


async def test_client_supplied_redaction_markers_are_discarded(api: Api) -> None:
    forged = sdk_event(1)
    forged["attributes"].update(
        {"redaction.server_count": 0, "redaction.server_kinds": [], "redaction.dropped_reason": "x"}
    )
    clean = wire_event(
        make_run_ids(), 1,
        attributes={
            "tool.name": "t",
            "redaction.server_count": 99,
            "redaction.dropped_reason": "too_large",
        },
    )  # fmt: skip
    await api.post_batch([forged, clean])
    assert (await stored(api, forged["event_id"])).attributes["redaction.server_count"] == 7
    assert "redaction.dropped_reason" not in (await stored(api, forged["event_id"])).attributes
    markers = (await stored(api, clean["event_id"])).attributes
    assert markers == {"tool.name": "t"}


# ---------------------------------------------------------------------- METADATA_ONLY


async def test_metadata_only_stores_no_payload_even_when_the_sdk_sends_one(api: Api) -> None:
    await set_mode(api, CaptureMode.METADATA_ONLY)
    event = sdk_event(1)
    assert (await api.post_batch([event])).json()["accepted"] == 1
    row = await stored(api, event["event_id"])
    assert row.payload is None and row.payload_ref is None
    a = row.attributes
    for gone in ("shell.stdout_artifact", "diff.artifact"):
        assert gone not in a
    assert a["redaction.dropped_reason"] == "mode_metadata_only"
    # metadata stays, as in the SDK's mode, but is still scanned
    assert a["tool.name"] == "shell" and a["metadata.owner"] == "alice@example.com"
    assert AWS_SENTINEL not in a["shell.command"] and GITHUB_SENTINEL not in a["error.message"]
    assert a["redaction.server_count"] == 3  # command, error message, tag
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_a_project_without_a_settings_row_is_metadata_only(api: Api) -> None:
    async with api.engine.begin() as conn:
        await conn.execute(text("DELETE FROM project_security_settings"))
    event = sdk_event(1)
    await api.post_batch([event])
    row = await stored(api, event["event_id"])
    assert row.payload is None
    assert row.attributes["redaction.dropped_reason"] == "mode_metadata_only"


async def test_an_event_with_nothing_to_drop_is_not_marked(api: Api) -> None:
    await set_mode(api, CaptureMode.METADATA_ONLY)
    event = wire_event(make_run_ids(), 1, attributes={"tool.name": "t"})
    await api.post_batch([event])
    assert (await stored(api, event["event_id"])).attributes == {"tool.name": "t"}


# -------------------------------------------------------------------------- DISABLED


async def test_disabled_also_drops_free_text(api: Api) -> None:
    await set_mode(api, CaptureMode.DISABLED)
    event = sdk_event(1)
    await api.post_batch([event])
    row = await stored(api, event["event_id"])
    a = row.attributes
    assert row.payload is None and row.payload_ref is None
    assert "error.message" not in a and "metadata.owner" not in a
    assert "shell.stdout_artifact" not in a
    assert a["redaction.dropped_reason"] == "mode_disabled"
    assert a["tool.name"] == "shell" and "shell.command" in a  # same as the SDK's DISABLED
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_modes_are_per_project(api: Api) -> None:
    await set_mode(api, CaptureMode.DISABLED, "alpha")
    alpha, beta = sdk_event(1), sdk_event(1)
    assert (await api.post_batch([alpha], token="writer")).json()["accepted"] == 1
    assert (await api.post_batch([beta], token="beta")).json()["accepted"] == 1
    assert (await stored(api, alpha["event_id"])).payload is None
    assert (await stored(api, beta["event_id"])).payload is not None  # beta is FULL


async def test_a_mode_change_applies_to_the_next_batch(api: Api) -> None:
    first, second = sdk_event(1), sdk_event(2)
    await api.post_batch([first])
    await set_mode(api, CaptureMode.METADATA_ONLY)
    await api.post_batch([second])
    assert (await stored(api, first["event_id"])).payload is not None
    assert (await stored(api, second["event_id"])).payload is None


# ---------------------------------------------------------------------- fail closed


@pytest.fixture
def broken_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scanner that fails on text containing BREAK and reports HUGE as too large."""
    original = SecretScanner.scan_text

    def scan_text(self: SecretScanner, text: str, budget: Any, **kwargs: Any) -> Any:
        if "BREAK" in text:
            raise ScanFailed("simulated")
        if "HUGE" in text:
            raise ScanTooLarge("simulated")
        return original(self, text, budget, **kwargs)

    monkeypatch.setattr(SecretScanner, "scan_text", scan_text)


async def test_a_scanner_failure_drops_the_value_and_keeps_the_event(
    api: Api, broken_scanner: None
) -> None:
    event = sdk_event(1)
    event["attributes"]["note"] = f"BREAK {AWS_SENTINEL}"
    event["payload"]["extra"] = "BREAK"
    response = await api.post_batch([event])
    assert response.status_code == 202 and response.json()["accepted"] == 1
    row = await stored(api, event["event_id"])
    assert "note" not in row.attributes  # dropped, not stored unscanned
    assert row.payload is None  # the whole payload goes
    assert row.attributes["redaction.dropped_reason"] == "scan_failed"
    assert row.attributes["tool.name"] == "shell"  # metadata survives
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_an_oversized_value_is_marked_too_large(api: Api, broken_scanner: None) -> None:
    event = sdk_event(1)
    event["payload"]["blob"] = "HUGE"
    await api.post_batch([event])
    row = await stored(api, event["event_id"])
    assert row.payload is None
    assert row.attributes["redaction.dropped_reason"] == "too_large"
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_the_log_line_counts_time_budget_and_size_drops_apart(
    api: Api, broken_scanner: None, caplog: pytest.LogCaptureFixture
) -> None:
    """`scan_failed` (time budget, scanner error) is a capacity signal; `too_large` is the client's
    payload. The log line carries both counts, never a value (review P2-3)."""
    failed, huge, clean = sdk_event(1), sdk_event(2), sdk_event(3)
    failed["payload"]["x"] = "BREAK"
    huge["payload"]["x"] = "HUGE"
    with caplog.at_level("INFO", logger="abb_api.ingestion.service"):
        assert (await api.post_batch([failed, huge, clean])).json()["accepted"] == 3
    (record,) = [r for r in caplog.records if r.getMessage() == "server redaction applied"]
    assert record.__dict__["scan_failed"] == 1 and record.__dict__["too_large"] == 1
    assert "BREAK" not in caplog.text and "HUGE" not in caplog.text


async def test_a_required_attribute_that_cannot_be_scanned_stays_as_a_marker(
    api: Api, broken_scanner: None
) -> None:
    event = wire_event(
        make_run_ids(), 1, event_type="shell.command.completed",
        attributes={"shell.command": f"BREAK {AWS_SENTINEL}"},
    )  # fmt: skip
    assert (await api.post_batch([event])).json()["accepted"] == 1
    row = await stored(api, event["event_id"])
    assert row.attributes["shell.command"] == "[dropped]"
    assert AWS_SENTINEL not in json.dumps(row.attributes)
    # the stored event is valid: it can be read back (the summarizer re-validates every event)
    from abb_api.db.event_rows import row_to_event

    assert row_to_event(row).attributes["shell.command"] == "[dropped]"


# --------------------------------------------------------------------- idempotency


async def test_the_same_event_redelivered_is_a_duplicate_with_identical_storage(api: Api) -> None:
    event = sdk_event(1)
    first = (await api.post_batch([event])).json()
    again = (await api.post_batch([event])).json()
    assert (first["accepted"], again["accepted"], again["duplicates"]) == (1, 0, 1)
    assert again["conflicts"] == 0
    row = await stored(api, event["event_id"])
    in_batch = (await api.post_batch([event, event])).json()
    assert (in_batch["duplicates"], in_batch["conflicts"]) == (2, 0)
    assert await stored(api, event["event_id"]) == row


async def test_redaction_is_deterministic_across_projects_and_orders(api: Api) -> None:
    run = make_run_ids()
    events = [sdk_event(n, run) for n in (1, 2, 3)]
    await api.post_batch(list(reversed(events)))  # out of order
    await api.post_batch(events)  # and redelivered in order
    async with api.engine.connect() as conn:
        rows = (
            await conn.execute(select(t.events).where(t.events.c.run_id == uuid_of(run["run_id"])))
        ).all()
    assert len(rows) == 3
    assert all(r.attributes["redaction.server_count"] == 7 for r in rows)
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_a_redelivery_after_the_mode_changed_keeps_the_first_version(api: Api) -> None:
    event = sdk_event(1)
    await api.post_batch([event])
    before = await stored(api, event["event_id"])
    await set_mode(api, CaptureMode.METADATA_ONLY)
    again = (await api.post_batch([event])).json()
    assert (again["accepted"], again["conflicts"], again["rejected"]) == (0, 1, 0)
    assert await stored(api, event["event_id"]) == before  # the first accepted version wins

    other = sdk_event(2)
    await api.post_batch([other])  # METADATA_ONLY first
    kept = await stored(api, other["event_id"])
    await set_mode(api, CaptureMode.FULL)
    assert (await api.post_batch([other])).json()["conflicts"] == 1
    assert await stored(api, other["event_id"]) == kept and kept.payload is None


async def test_rejected_events_never_reach_the_settings_or_scanner(api: Api) -> None:
    bad = sdk_event(1)
    bad["event_type"] = "Not Valid"
    good = sdk_event(2)
    body = (await api.post_batch([bad, good])).json()
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert await find_in_database(api.engine, SECRETS) == {}


async def test_settings_that_cannot_be_read_stop_the_batch_with_a_retryable_error(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abb_api.security.settings import PgSecuritySettingsStore

    async def down(*_: object, **__: object) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(PgSecuritySettingsStore, "get", down)
    event = sdk_event(1)
    response = await api.post_batch([event])
    assert response.status_code == 503
    monkeypatch.undo()
    async with api.engine.connect() as conn:
        assert (await conn.execute(select(t.events))).first() is None


# -------------------------------------------------------------------------- artifacts


async def put_artifact(
    api: Api, body: bytes, *, artifact_id: str | None = None, token: str = "art", **query: Any
) -> Any:
    artifact_id = artifact_id or new_id(IdKind.ARTIFACT)
    response = await api.client.put(
        f"/v1/artifacts/{artifact_id}",
        content=body,
        params={"run_id": new_id(IdKind.RUN), "kind": "stdout", **query},
        headers={"content-type": "text/plain", "authorization": f"Bearer {api.tokens[token]}"},
    )
    response.artifact_id = artifact_id  # type: ignore[attr-defined]
    return response


@pytest.fixture
async def art(api_store: Api) -> Api:
    from abb_api.auth import scopes
    from abb_api.auth.repository import ApiKeyRepository

    async with api_store.engine.begin() as conn:
        keys = ApiKeyRepository(conn, api_store.tenant.context)
        api_store.tokens["art"] = (
            await keys.create(
                scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                project_id=api_store.tenant.project_uuids["alpha"],
            )
        ).token
        api_store.tokens["art_beta"] = (
            await keys.create(
                scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                project_id=api_store.tenant.project_uuids["beta"],
            )
        ).token
    return api_store


def store_dir(api: Api) -> Path:
    return Path(api.app.state.artifacts._store._root)


async def record(api: Api, artifact_id: str) -> Any:
    async with api.engine.connect() as conn:
        return (
            await conn.execute(select(t.artifacts).where(t.artifacts.c.id == uuid_of(artifact_id)))
        ).one()


async def read_back(api: Api, response: Any) -> str:
    from abb_api.artifacts.service import ArtifactService

    service: ArtifactService = api.app.state.artifacts
    record_ = await record(api, response.artifact_id)
    data = await service._store.read(record_.storage_uri, 0, record_.size_bytes)
    return data.decode()


OUTPUT = f"$ env\nHOME=/home/a\nAWS_ACCESS_KEY_ID={AWS_SENTINEL}\n{PRIVATE_KEY_SENTINEL}\ndone\n"


async def test_full_redacts_an_artifact_before_storing_it(art: Api) -> None:
    response = await put_artifact(art, OUTPUT.encode())
    assert response.status_code == 201, response.text
    body = await read_back(art, response)
    assert AWS_SENTINEL not in body and KEY_BODY_SENTINEL not in body
    assert "[REDACTED:" in body and "HOME=/home/a" in body and body.endswith("done\n")
    meta = response.json()
    assert meta["size_bytes"] == len(body.encode())
    import hashlib

    assert meta["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    row = await record(art, response.artifact_id)
    assert row.metadata["redaction"]["server_count"] >= 2
    assert "private_key" in row.metadata["redaction"]["server_kinds"]
    assert "dropped_reason" not in row.metadata["redaction"]
    assert find_on_disk(store_dir(art), SECRETS) == {}
    assert await find_in_database(art.engine, SECRETS) == {}


async def test_a_clean_artifact_is_stored_byte_for_byte(art: Api) -> None:
    data = "ran 3 tests\nOK ✓\n".encode()
    response = await put_artifact(art, data)
    assert response.status_code == 201
    assert await read_back(art, response) == data.decode()
    assert (await record(art, response.artifact_id)).metadata == {}


@pytest.mark.parametrize(
    ("mode", "reason"),
    [(CaptureMode.METADATA_ONLY, "mode_metadata_only"), (CaptureMode.DISABLED, "mode_disabled")],
)
async def test_other_modes_accept_the_artifact_without_its_body(
    art: Api, mode: CaptureMode, reason: str
) -> None:
    await set_mode(art, mode)
    response = await put_artifact(art, OUTPUT.encode(), name="out")
    assert response.status_code == 201
    body = await read_back(art, response)
    assert reason in body and AWS_SENTINEL not in body
    row = await record(art, response.artifact_id)
    assert row.metadata["redaction"]["dropped_reason"] == reason
    assert row.size_bytes == len(body.encode()) and row.artifact_type == "stdout"
    assert find_on_disk(store_dir(art), SECRETS) == {}


async def test_a_body_beyond_the_scan_budget_is_dropped_as_too_large(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    settings = make_settings(
        database_url, artifact_dir=str(tmp_path / "s"), security_scan_artifact_max_bytes=2048
    )
    async for api in build_api(database_url, engine, settings=settings):
        await _grant(api)
        response = await put_artifact(api, (OUTPUT + "x" * 3000).encode())
        assert response.status_code == 201
        row = await record(api, response.artifact_id)
        assert row.metadata["redaction"]["dropped_reason"] == "too_large"
        assert "too_large" in await read_back(api, response)
        assert find_on_disk(tmp_path / "s", SECRETS) == {}


async def _grant(api: Api) -> None:
    from abb_api.auth import scopes
    from abb_api.auth.repository import ApiKeyRepository

    async with api.engine.begin() as conn:
        api.tokens["art"] = (
            await ApiKeyRepository(conn, api.tenant.context).create(
                scopes=frozenset({scopes.ARTIFACTS_WRITE}),
                project_id=api.tenant.project_uuids["alpha"],
            )
        ).token


async def test_an_unsplittable_line_fails_closed(art: Api) -> None:
    body = ("a" * 70000 + "\n" + OUTPUT).encode()  # one 70k line with no separator
    response = await put_artifact(art, body)
    assert response.status_code == 201
    assert (await record(art, response.artifact_id)).metadata["redaction"][
        "dropped_reason"
    ] == "too_large"
    assert find_on_disk(store_dir(art), SECRETS) == {}


async def test_a_scanner_error_drops_the_artifact_body(
    art: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: SecretScanner, *_: object, **__: object) -> None:
        raise ScanFailed("simulated")

    monkeypatch.setattr(SecretScanner, "scan_bytes", broken)
    response = await put_artifact(art, OUTPUT.encode())
    assert response.status_code == 201
    assert (await record(art, response.artifact_id)).metadata["redaction"][
        "dropped_reason"
    ] == "scan_failed"
    assert find_on_disk(store_dir(art), SECRETS) == {}


async def test_an_artifact_retry_is_a_duplicate_even_after_the_mode_changed(art: Api) -> None:
    first = await put_artifact(art, OUTPUT.encode())
    assert first.status_code == 201
    same = await put_artifact(art, OUTPUT.encode(), artifact_id=first.artifact_id)
    assert same.status_code == 200 and same.json() == first.json()
    await set_mode(art, CaptureMode.METADATA_ONLY)
    later = await put_artifact(art, OUTPUT.encode(), artifact_id=first.artifact_id)
    assert later.status_code == 200 and later.json() == first.json()  # first version wins
    different = await put_artifact(art, b"something else", artifact_id=first.artifact_id)
    assert different.status_code == 409
    assert find_on_disk(store_dir(art), SECRETS) == {}


async def test_a_dropped_artifact_retried_in_full_mode_stays_dropped(art: Api) -> None:
    await set_mode(art, CaptureMode.METADATA_ONLY)
    first = await put_artifact(art, OUTPUT.encode())
    await set_mode(art, CaptureMode.FULL)
    again = await put_artifact(art, OUTPUT.encode(), artifact_id=first.artifact_id)
    assert again.status_code == 200 and again.json() == first.json()
    assert "mode_metadata_only" in await read_back(art, first)


async def test_the_claimed_hash_is_checked_against_what_was_sent_not_what_is_stored(
    art: Api,
) -> None:
    import hashlib

    ok = await put_artifact(art, OUTPUT.encode(), **{})
    assert ok.status_code == 201
    raw_hash = hashlib.sha256(OUTPUT.encode()).hexdigest()
    headers = {
        "content-type": "text/plain",
        "authorization": f"Bearer {art.tokens['art']}",
        "x-content-sha256": raw_hash,
    }
    sent = await art.client.put(
        f"/v1/artifacts/{new_id(IdKind.ARTIFACT)}",
        content=OUTPUT.encode(),
        params={"run_id": new_id(IdKind.RUN), "kind": "stdout"},
        headers=headers,
    )
    assert sent.status_code == 201 and sent.json()["sha256"] != raw_hash
    headers["x-content-sha256"] = "0" * 64
    bad = await art.client.put(
        f"/v1/artifacts/{new_id(IdKind.ARTIFACT)}",
        content=OUTPUT.encode(),
        params={"run_id": new_id(IdKind.RUN), "kind": "stdout"},
        headers=headers,
    )
    assert bad.status_code == 422


async def test_modes_apply_to_artifacts_per_project(art: Api) -> None:
    await set_mode(art, CaptureMode.METADATA_ONLY, "alpha")
    alpha = await put_artifact(art, OUTPUT.encode(), token="art")
    beta = await put_artifact(art, OUTPUT.encode(), token="art_beta")
    assert "mode_metadata_only" in await read_back(art, alpha)
    assert "[REDACTED:aws_access_key]" in await read_back(art, beta)
