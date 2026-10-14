"""Seed alert destinations, rules and notifications straight through the repositories."""

import uuid
from datetime import UTC, datetime
from typing import Any

from abb_event_schema.ids import IdKind
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.alerts import model as m
from abb_api.alerts import sealed
from abb_api.alerts.repository import DestinationRepository, RuleRepository
from abb_api.alerts.targets import mask_recipients, validate_web_url
from abb_api.ids import new_uuid
from abb_api.tenancy import TenantContext
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event

SENTINEL_URL = "https://hooks.example.com/services/T000/B000/SENTINELSECRETTOKEN1234"
SENTINEL_SIGNING = "whsec_SENTINELSIGNINGSECRET"


async def seed_destination(
    engine: AsyncEngine,
    tenant: TenantContext,
    *,
    name: str = "pager",
    dest_type: str = "webhook",
    url: str = SENTINEL_URL,
    recipients: list[str] | None = None,
    state: str = "active",
) -> uuid.UUID:
    destination_id = new_uuid(IdKind.DESTINATION)
    if dest_type == "email":
        config: dict[str, Any] = {"recipients": recipients or ["oncall@example.com"]}
        masked = mask_recipients(config["recipients"])
    else:
        web = validate_web_url(url)
        config, masked = {"url": web.url}, web.masked
    blob = await sealed.seal(
        engine,
        sealed.encode_config(config),
        purpose="config",
        workspace_id=tenant.workspace_id,
        destination_id=destination_id,
    )
    signing = None
    if dest_type == "webhook":
        signing = await sealed.seal(
            engine,
            SENTINEL_SIGNING.encode(),
            purpose="signing",
            workspace_id=tenant.workspace_id,
            destination_id=destination_id,
        )
    async with engine.begin() as conn:
        await DestinationRepository(conn, tenant).insert(
            id=destination_id,
            name=name,
            type=dest_type,
            config_encrypted=blob,
            signing_secret_encrypted=signing,
            key_id=sealed.key_id_of(blob),
            masked_target=masked,
            state=state,
            created_by="cli:seed",
            updated_by="cli:seed",
        )
    return destination_id


async def seed_rule(
    engine: AsyncEngine,
    tenant: TenantContext,
    destination_ids: list[uuid.UUID],
    *,
    name: str = "rule",
    trigger: str = m.RUN_FAILED,
    project_id: uuid.UUID | None = None,
    filters: dict[str, Any] | None = None,
    cooldown_seconds: int = 0,
    enabled: bool = True,
) -> uuid.UUID:
    rule_id = new_uuid(IdKind.ALERT_RULE)
    async with engine.begin() as conn:
        await RuleRepository(conn, tenant).insert(
            destination_ids,
            id=rule_id,
            project_id=project_id,
            name=name,
            trigger=trigger,
            filters=filters or {},
            cooldown_seconds=cooldown_seconds,
            enabled=enabled,
            created_by="cli:seed",
            updated_by="cli:seed",
        )
    return rule_id


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


async def rows(api: Api, where: str = "true") -> list[Any]:
    async with api.engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT * FROM alert_notifications WHERE {where} ORDER BY created_at, id")  # noqa: S608
        )
        return list(result)


def final_event(ids: dict[str, str], kind: str, agent: str, message: str | None) -> dict[str, Any]:
    extra: dict[str, Any] = {"attributes": {"error.message": message}} if message else {}
    if kind == "FAILED":
        return wire_event(
            ids, 9, event_type="run.failed", agent_id=agent, span_id=..., status="error", **extra
        )
    status = {"TIMED_OUT": "timeout", "BLOCKED": "blocked", "SUCCESS": "success"}[kind]
    return wire_event(
        ids, 9, event_type="run.completed", agent_id=agent, span_id=..., status=status, **extra
    )


def run_events(
    kind: str, *, agent: str = "coding-agent", message: str | None = None, cost: float | None = None
) -> tuple[str, list[dict[str, Any]]]:
    ids = make_run_ids()
    events = [
        wire_event(ids, 1, event_type="run.started", agent_id=agent, span_id=..., attributes={})
    ]
    if cost is not None:
        events.append(
            wire_event(
                ids,
                2,
                event_type="llm.request.completed",
                agent_id=agent,
                status="success",
                duration_ms=5,
                attributes={
                    "llm.provider": "example-provider",
                    "llm.model": "alerts-test-model",
                    "llm.input_tokens": 9,
                    "cost.estimated_usd": cost,
                },
            )
        )
    events.append(final_event(ids, kind, agent, message))
    return ids["run_id"], events


async def post_run(api: Api, kind: str, token: str = "writer", **kwargs: Any) -> str:
    run_id, events = run_events(kind, **kwargs)
    assert (await api.post_batch(events, token=token)).status_code == 202
    return run_id
