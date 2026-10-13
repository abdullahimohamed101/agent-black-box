"""An artifact's name is free text a client can fill with anything: the server scans it like the
body (Phase 13 gate 2 found a raw upload that stored an AWS-style key in `artifacts.name`)."""

from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import select

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.db import tables as t
from abb_api.security.redaction import ServerRedactor
from tests.api_fixtures import Api
from tests.security_helpers import AWS_KEY, Run


async def upload(api: Api, run_id: str, *, name: str | None, body: bytes = b"ok\n") -> str:
    async with api.engine.begin() as conn:
        key = await ApiKeyRepository(conn, api.tenant.context).create(
            scopes=frozenset({scopes.ARTIFACTS_WRITE}), project_id=api.tenant.project_uuids["alpha"]
        )
    artifact_id = new_id(IdKind.ARTIFACT)
    params = {"run_id": run_id, "kind": "stdout"}
    if name is not None:
        params["name"] = name
    response = await api.client.put(
        f"/v1/artifacts/{artifact_id}",
        params=params,
        content=body,
        headers={"authorization": f"Bearer {key.token}", "content-type": "text/plain"},
    )
    assert response.status_code == 201, response.text
    return artifact_id


async def stored_names(api: Api) -> list[str | None]:
    async with api.engine.connect() as conn:
        return [r.name for r in await conn.execute(select(t.artifacts.c.name))]


async def test_a_secret_in_an_artifact_name_is_redacted_before_it_is_stored(api: Api) -> None:
    run = Run()
    assert (await api.post_batch(run.wires)).status_code == 202
    await upload(api, run.run_id, name=f"deploy-{AWS_KEY}.log")
    await upload(api, run.run_id, name="stdout")
    names = await stored_names(api)
    assert sorted(n for n in names if n) == ["deploy-[REDACTED:aws_access_key].log", "stdout"]
    assert not any(AWS_KEY in (n or "") for n in names)


def test_a_label_that_cannot_be_scanned_is_dropped_not_stored() -> None:
    assert ServerRedactor().label("plain name") == "plain name"
    assert ServerRedactor().label("x" * 5000) is None  # over the label budget
    assert ServerRedactor().label(f"a {AWS_KEY}", max_seconds=1e-9) is None  # out of time
