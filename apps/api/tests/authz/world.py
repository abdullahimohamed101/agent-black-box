"""Two tenants with distinctive data, every key kind, and one way to send any registry request."""

import asyncio
import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

import httpx
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import text

from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository, UserRepository
from abb_api.cost.repository import CostRepository
from abb_api.ids import new_uuid, public_id
from abb_api.workspaces.repository import InvitationRepository
from tests.api_fixtures import NOW, Api
from tests.auth_helpers import add_member, mint_session, remove_member, set_role
from tests.authz.registry import RequestSpec, RouteCase, Side
from tests.ingest_helpers import make_run_ids, wire_event
from tests.stream_fixtures import Live, serve

WEB_ORIGIN = "http://localhost:3000"
CANARY = "CANARY-GLOBEX-7f3e"
CANARY_SLUG = CANARY.lower()  # agent and model names are lower-case slugs
GLOBEX_RUNS, GLOBEX_RUN_COST = 7, 777.777
ACME_RUNS, ACME_RUN_COST = 3, 1.5


@dataclass
class World:
    api: Api
    live: Live
    acme: Side
    globex: Side
    # actor name -> the credential headers it sends (keys, people with sessions, and bad ones)
    actors: dict[str, dict[str, str]] = field(default_factory=dict)

    def headers(self, actor: str, spec: RequestSpec) -> dict[str, str]:
        return {**spec.headers, **self.actors[actor]}

    async def _fresh_target(self, kind: str, actor: str) -> str:
        """A new member, invitation or key in acme, for a request that consumes its target.

        A key is made by `actor` when that is one of the role actors, so `Own` grants can be
        exercised; otherwise it has no creator (as if made by the CLI).
        """
        workspace = self.api.tenant.context.workspace_id
        if kind == "api_key":
            async with self.api.engine.begin() as conn:
                creator = await UserRepository(conn).find_by_email(f"{actor}@acme.test")
                created = await ApiKeyRepository(conn, self.api.tenant.context).create(
                    scopes=frozenset({scopes.RUNS_READ}),
                    created_by=creator.id if creator else None,
                )
            return created.stored.key_id
        if kind == "member":
            user = await add_member(
                self.api.engine, workspace, f"target-{uuid.uuid4().hex[:10]}@acme.test", "DEVELOPER"
            )
            return public_id(IdKind.USER, user.id)
        invitation_id = new_uuid(IdKind.INVITATION)
        async with self.api.engine.begin() as conn:
            await InvitationRepository(conn, self.api.tenant.context).create(
                invitation_id,
                email=f"fresh-{uuid.uuid4().hex[:10]}@acme.test",
                role="VIEWER",
                token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                invited_by=None,
                expires_at=self.api.clock() + timedelta(days=7),
            )
        return public_id(IdKind.INVITATION, invitation_id)

    async def send(self, case: RouteCase, spec: RequestSpec, actor: str) -> httpx.Response:
        """Send over the case's transport; a stream is read until its headers, then closed."""
        if spec.fresh is not None:
            spec = replace(
                spec, path=spec.path.replace("FRESH", await self._fresh_target(spec.fresh, actor))
            )
        headers = self.headers(actor, spec)
        if case.transport == "socket":
            async with httpx.AsyncClient(base_url=self.live.base_url, timeout=10) as client:
                async with client.stream(
                    "GET", spec.path, params=spec.params, headers=headers
                ) as r:
                    if r.status_code != 200:
                        await r.aread()
                    return r
        response = await self.api.client.request(
            spec.method, spec.path, params=spec.params, content=spec.content, headers=headers
        )
        self.api.client.cookies.clear()
        return response

    async def get(self, path: str, actor: str, **params: str | int) -> httpx.Response:
        response = await self.api.client.get(
            path, params={k: str(v) for k, v in params.items()}, headers=self.actors[actor]
        )
        self.api.client.cookies.clear()
        return response

    async def read_stream(self, run_id: str, actor: str, seconds: float = 1.2) -> str:
        """The raw text of a stream for about two polls (the fixture polls every 0.2 s)."""
        chunks: list[str] = []

        async def read() -> None:
            async with httpx.AsyncClient(base_url=self.live.base_url, timeout=10) as client:
                path = f"/v1/runs/{run_id}/stream"
                async with client.stream("GET", path, headers=self.actors[actor]) as r:
                    async for line in r.aiter_lines():
                        chunks.append(line)

        try:
            await asyncio.wait_for(read(), seconds)
        except TimeoutError:
            pass
        return "\n".join(chunks)


async def _ingest_run(api: Api, token: str, tag: str, cost: float, model: str) -> tuple[str, str]:
    ids = make_run_ids()
    llm = {
        "llm.provider": "example-provider",
        "llm.model": model,
        "llm.input_tokens": 9,
        "cost.estimated_usd": cost,
    }
    llm_id = new_id(IdKind.EVENT)
    events = [
        wire_event(ids, 1, event_type="run.started", agent_id=tag, span_id=..., attributes={}),
        wire_event(
            ids, 2, event_type="llm.request.completed", agent_id=tag, event_id=llm_id,
            status="success", duration_ms=500, attributes=llm,
        ),
        wire_event(
            ids, 3, event_type="tool.call.completed", agent_id=tag, status="success",
            duration_ms=10, attributes={"tool.name": f"{tag}-tool"},
        ),
        wire_event(
            ids, 4, event_type="run.completed", agent_id=tag, span_id=..., status="success",
            attributes={},
        ),
    ]  # fmt: skip
    response = await api.post_batch(events, token=token)
    assert response.status_code == 202, response.text
    return ids["run_id"], llm_id


async def _upload(api: Api, token: str, run_id: str, name: str, body: str) -> str:
    artifact_id = new_id(IdKind.ARTIFACT)
    response = await api.client.put(
        f"/v1/artifacts/{artifact_id}",
        params={"run_id": run_id, "kind": "stdout", "name": name},
        content=body.encode(),
        headers={
            "authorization": f"Bearer {api.tokens[token]}",
            "content-type": "text/plain",
        },
    )
    assert response.status_code == 201, response.text
    return artifact_id


async def build_world(api: Api, runtime_database_url: str) -> AsyncIterator[World]:
    async with api.engine.begin() as conn:
        for name, tenant, kind in (
            ("art_alpha", api.tenant, "alpha"),
            ("art_wide", api.tenant, None),
            ("art_other", api.other, "p"),
        ):
            project = tenant.project_uuids[kind] if kind else None
            created = await ApiKeyRepository(conn, tenant.context).create(
                scopes=frozenset({scopes.ARTIFACTS_WRITE}), project_id=project
            )
            api.tokens[name] = created.token
        # The canary lives in every free-text field a globex user could see: project name and
        # slug, agent and model names, tool name, artifact name and content, price notes.
        await conn.execute(
            text("UPDATE projects SET name = :n, slug = :s WHERE workspace_id = :w"),
            {"n": CANARY, "s": CANARY_SLUG, "w": api.other.context.workspace_id},
        )
        await CostRepository(conn, api.other.context).add_override(
            project_id=None, provider="example-provider", model_pattern=f"{CANARY_SLUG}*",
            input_per_million=Decimal(1), output_per_million=Decimal(1),
            cached_input_per_million=None,
            request_price=Decimal(0), valid_from=NOW, note=CANARY,
        )  # fmt: skip

        # Audit rows carry free text too (details), so globex's hold the canary.
        for tenant, detail in ((api.tenant, "acme-note"), (api.other, CANARY_SLUG)):
            await AuditRepository(conn, tenant.context).append(
                AuditEntry("cli", "cli:seed", "project.create", details={"slug": detail})
            )

    sides: dict[str, Side] = {}
    for name, token, art_token, tag, count, cost, tenant in (
        ("acme", "writer", "art_alpha", "acme-agent", ACME_RUNS, ACME_RUN_COST, api.tenant),
        ("globex", "other", "art_other", CANARY_SLUG, GLOBEX_RUNS, GLOBEX_RUN_COST, api.other),
    ):
        run_ids: list[str] = []
        first_event = ""
        for _ in range(count):
            run_id, event_id = await _ingest_run(api, token, tag, cost, f"{tag}-model")
            run_ids.append(run_id)
            first_event = first_event or event_id
        label = CANARY if name == "globex" else "acme"
        workspace_uuid = tenant.context.workspace_id
        member = await add_member(
            api.engine, workspace_uuid, f"{label.lower()}-member@{name}.test", "DEVELOPER"
        )
        invitation_id = new_uuid(IdKind.INVITATION)
        async with api.engine.begin() as conn:
            await InvitationRepository(conn, tenant.context).create(
                invitation_id,
                email=f"{label.lower()}-invitee@{name}.test",
                role="VIEWER",
                token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                invited_by=None,
                expires_at=api.clock() + timedelta(days=7),
            )
        async with api.engine.begin() as conn:
            seeded_key = await ApiKeyRepository(conn, tenant.context).create(
                scopes=frozenset({scopes.RUNS_READ}), name=f"{label}-key"
            )
        artifact_id = await _upload(api, art_token, run_ids[0], f"{label}.txt", f"{label} output")
        public_project = tenant.projects["alpha" if name == "acme" else "p"]
        sides[name] = Side(
            name=name,
            workspace_id=tenant.workspace_id,
            project_id=public_project,
            run_id=run_ids[0],
            event_id=first_event,
            artifact_id=artifact_id,
            user_id=public_id(IdKind.USER, member.id),
            invitation_id=public_id(IdKind.INVITATION, invitation_id),
            key_id=seeded_key.stored.key_id,
            every_id=frozenset(
                {
                    tenant.workspace_id,
                    public_project,
                    artifact_id,
                    *run_ids,
                    first_event,
                    public_id(IdKind.USER, member.id),
                    public_id(IdKind.INVITATION, invitation_id),
                }
            ),
        )
    await api.drain()
    async for live in serve(api, runtime_database_url, web_origin=WEB_ORIGIN):
        world = World(api, live, sides["acme"], sides["globex"])
        world.actors = await _actors(api, sides["acme"])
        yield world


ROLE_ACTORS = ("owner", "admin", "developer", "viewer", "security", "billing")
USER_ACTORS = (
    *ROLE_ACTORS, "no_membership", "removed_member", "downgraded", "expired_session",
    "revoked_session", "session_as_bearer", "key_as_cookie", "owner_no_header",
)  # fmt: skip


async def _actors(api: Api, acme: Side) -> dict[str, dict[str, str]]:
    """Every kind of caller, as the headers it sends."""
    workspace = api.tenant.context.workspace_id
    now = api.clock()
    actors: dict[str, dict[str, str]] = {
        name: {"authorization": f"Bearer {token}"} for name, token in api.tokens.items()
    }
    actors["none"] = {}
    actors["malformed"] = {"authorization": "Bearer not-a-key"}

    def person(token: str, *, header: bool = True) -> dict[str, str]:
        return {
            "cookie": f"abb_session={token}",
            "origin": WEB_ORIGIN,
            **({"x-abb-workspace": acme.workspace_id} if header else {}),
        }

    for role in ROLE_ACTORS:
        user = await add_member(api.engine, workspace, f"{role}@acme.test", role.upper())
        actors[role] = person(await mint_session(api.engine, user.id, now))
    stranger = await add_member(api.engine, workspace, "stranger@elsewhere.test", None)
    actors["no_membership"] = person(await mint_session(api.engine, stranger.id, now))
    leaver = await add_member(api.engine, workspace, "leaver@acme.test", "OWNER")
    actors["removed_member"] = person(await mint_session(api.engine, leaver.id, now))
    await remove_member(api.engine, workspace, leaver.id)
    demoted = await add_member(api.engine, workspace, "demoted@acme.test", "OWNER")
    actors["downgraded"] = person(await mint_session(api.engine, demoted.id, now))
    await set_role(api.engine, workspace, demoted.id, "VIEWER")
    lapsed = await add_member(api.engine, workspace, "lapsed@acme.test", "OWNER")
    actors["expired_session"] = person(
        await mint_session(api.engine, lapsed.id, now, absolute=timedelta(seconds=-1))
    )
    revoked = await add_member(api.engine, workspace, "revoked@acme.test", "OWNER")
    token = await mint_session(api.engine, revoked.id, now)
    async with api.engine.begin() as conn:
        await conn.execute(
            text("UPDATE sessions SET revoked_at = now() WHERE user_id = :u"), {"u": revoked.id}
        )
    actors["revoked_session"] = person(token)
    owner_token = actors["owner"]["cookie"].split("=", 1)[1]
    actors["session_as_bearer"] = {
        "authorization": f"Bearer {owner_token}",
        "x-abb-workspace": acme.workspace_id,
    }
    actors["key_as_cookie"] = person(api.tokens["reader"])
    actors["owner_no_header"] = person(owner_token, header=False)
    # A member of both workspaces, acting in acme: must see exactly what an acme member sees.
    dual = await add_member(api.engine, workspace, "dual@both.test", "OWNER")
    await add_member(api.engine, api.other.context.workspace_id, "dual@both.test", "OWNER")
    actors["dual"] = person(await mint_session(api.engine, dual.id, now))
    return actors


def outcome(case: RouteCase, response: httpx.Response) -> str:
    """Reduce a response to the vocabulary of the literal tables: allow, deny, 401, 404."""
    if response.status_code == case.success:
        return "allow"
    return {400: "400", 401: "401", 403: "deny", 404: "404"}.get(
        response.status_code, f"other:{response.status_code}"
    )


def body_text(response: httpx.Response) -> str:
    try:
        return json.dumps(response.json())
    except ValueError:
        return response.text
