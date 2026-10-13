"""Builders for the decision and approvals tests: a policy that gates actions, intents, people."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import text

from abb_api.auth import scopes
from abb_api.auth.keys import parse_key
from abb_api.auth.repository import ApiKeyRepository
from abb_api.ids import parse_public_id
from abb_api.policies.service import publish_policy
from abb_api.security.settings import Actor
from tests.api_fixtures import Api, person

POLICY = """\
version: 1
policies:
  - id: deny-env-read
    description: Do not read dotenv files.
    when: {capability: filesystem.read, resource: "**/.env*"}
    decision: deny
  - id: gate-push
    description: Pushing to main needs a person.
    when: {capability: github.push, resource: main}
    decision: require_approval
    expires_in: 15m
  - id: gate-deploy
    description: Production deploys need a security approver.
    when: {capability: deploy.run}
    decision: require_approval
    approver_role: security
    expires_in: 1h
    on_expiry: allow
    on_error: closed
  - id: gate-self
    description: A person may approve their own agent here.
    when: {capability: notes.write}
    decision: require_approval
    allow_self_approval: true
  - id: gate-owner
    description: Only an owner may approve this.
    when: {capability: billing.change}
    decision: require_approval
    approver_role: owner
  - id: gate-dev
    description: A developer may approve this.
    when: {capability: lint.fix}
    decision: require_approval
    approver_role: developer
"""

SENTINEL = "SENTINEL-do-not-leak-9f3a"


def intent(run_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    """An action intent for the gated push (override any field)."""
    body: dict[str, Any] = {
        "run_id": run_id or new_id(IdKind.RUN),
        "agent_id": "coding-agent",
        "capability": "github.push",
        "resource": "main",
        "risk_class": "R3",
        "arguments": {"remote": "origin", "force": False},
    }
    body.update(overrides)
    return body


async def publish(api: Api, document: str = POLICY, *, project: str | None = "alpha") -> None:
    scope = api.tenant.project_uuids[project] if project else None
    async with api.engine.begin() as conn:
        await publish_policy(conn, api.tenant.context, scope, Actor("cli", "cli:test"), document)


async def decide(api: Api, body: dict[str, Any], token: str = "writer") -> httpx.Response:
    return await api.client.post("/v1/policy/decide", json=body, headers=api.headers(token))


async def ask(api: Api, run_id: str | None = None, **overrides: Any) -> tuple[dict[str, Any], str]:
    """Decide a gated action; returns the intent and the approval id."""
    body = intent(run_id, **overrides)
    response = await decide(api, body)
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "require_approval", response.text
    return body, str(response.json()["approval_id"])


async def consume(
    api: Api, approval_id: str, body: dict[str, Any], token: str = "writer"
) -> httpx.Response:
    return await api.client.post(
        f"/v1/approvals/{approval_id}/consume", json={"intent": body}, headers=api.headers(token)
    )


async def decision(
    api: Api, headers: dict[str, str], approval_id: str, verdict: str = "approve", **extra: Any
) -> httpx.Response:
    return await api.client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": verdict, **extra},
        headers=headers,
    )


async def member(api: Api, email: str, role: str, workspace: str = "acme") -> dict[str, str]:
    """Headers of a signed-in member; the same person again gets a new session, not a new row."""
    from abb_api.auth.repository import UserRepository
    from tests.auth_helpers import mint_session

    tenant = api.tenant if workspace == "acme" else api.other
    async with api.engine.connect() as conn:
        user = await UserRepository(conn).find_by_email(email)
        known = (
            user is not None
            and (
                await conn.execute(
                    text(
                        "SELECT 1 FROM workspace_members WHERE workspace_id = :w AND user_id = :u"
                    ),
                    {"w": tenant.context.workspace_id, "u": user.id},
                )
            ).first()
        )
    if not known:
        return await person(api, email, role, workspace)
    assert user is not None
    token = await mint_session(api.engine, user.id, api.clock())
    return {
        "cookie": f"abb_session={token}",
        "origin": "http://localhost:3000",
        "x-abb-workspace": tenant.workspace_id,
    }


async def approve(api: Api, approval_id: str, email: str = "admin@acme.test") -> httpx.Response:
    headers = await member(api, email, "ADMIN")
    return await decision(api, headers, approval_id, "approve")


async def approved(
    api: Api, run_id: str | None = None, **overrides: Any
) -> tuple[dict[str, Any], str]:
    body, approval_id = await ask(api, run_id, **overrides)
    assert (await approve(api, approval_id)).status_code == 200
    return body, approval_id


def key_id_of(api: Api, token: str) -> str:
    parsed = parse_key(api.tokens[token])
    assert parsed is not None
    return parsed.key_id


async def key_made_by(api: Api, email: str) -> str:
    """A project key (alpha, events:write) created by an existing member; returns its token name."""
    from abb_api.auth.repository import UserRepository

    async with api.engine.begin() as conn:
        user = await UserRepository(conn).find_by_email(email)
        assert user is not None, "sign the member in first (person())"
        created = await ApiKeyRepository(conn, api.tenant.context).create(
            scopes=frozenset({scopes.EVENTS_WRITE}),
            project_id=api.tenant.project_uuids["alpha"],
            created_by=user.id,
        )
    token = f"key-by-{email}"
    api.tokens[token] = created.token
    return token


async def approval_row(api: Api, approval_id: str) -> dict[str, Any]:
    parsed = parse_public_id(IdKind.APPROVAL, approval_id)
    async with api.engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT * FROM approvals WHERE id = :i"), {"i": parsed})
        ).one()
    return dict(row._mapping)


async def events_of(api: Api, run_id: str, prefix: str = "") -> list[Any]:
    async with api.engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT event_type, attributes, sdk, event_id FROM events WHERE run_id = :r "
                "ORDER BY occurred_at, received_at, event_id"
            ),
            {"r": parse_public_id(IdKind.RUN, run_id)},
        )
        return [r for r in rows if r.event_type.startswith(prefix)]


@dataclass(frozen=True)
class Actors:
    admin: dict[str, str]
    owner: dict[str, str]


def later(api: Api, **delta: float) -> None:
    api.clock.now = api.clock.now + timedelta(**delta)


def fresh_uuid() -> uuid.UUID:
    return uuid.uuid4()
