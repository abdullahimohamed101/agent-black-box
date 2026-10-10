"""API key repository and authentication, against a real database."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes
from abb_api.auth.repository import LAST_USED_RESOLUTION, ApiKeyLookup, ApiKeyRepository
from abb_api.auth.service import authenticate
from abb_api.authz.matrix import scope_actions
from abb_api.authz.service import PermissionDenied, authorize
from abb_api.core.domain import NotFoundError
from abb_api.core.errors import AppError
from abb_api.projects.repository import ProjectRepository
from abb_api.tenancy import TenantContext
from abb_api.workspaces import WorkspaceProvisioning

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
ALL = frozenset({scopes.EVENTS_WRITE, scopes.RUNS_READ})


class Tick:
    """A manually advanced clock."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def setup_tenants(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        provisioning = WorkspaceProvisioning(conn)
        a = TenantContext((await provisioning.create(name="A", slug="acme")).id)
        b = TenantContext((await provisioning.create(name="B", slug="globex")).id)
        project_a = await ProjectRepository(conn, a).create(name="P", slug="p")
        project_b = await ProjectRepository(conn, b).create(name="P", slug="p")
    return a, b, project_a, project_b


async def test_create_and_authenticate_a_project_key(engine: AsyncEngine) -> None:
    a, _, project_a, _ = await setup_tenants(engine)
    async with engine.begin() as conn:
        created = await ApiKeyRepository(conn, a).create(scopes=ALL, project_id=project_a.id)
    async with engine.begin() as conn:
        principal = await authenticate(conn, created.token, Tick())
    assert principal.workspace_id == a.workspace_id
    assert principal.project_id == project_a.id
    assert principal.kind == "api_key"
    assert principal.actions == scope_actions(ALL)
    assert principal.actor_id == f"key:{created.stored.key_id}"
    assert created.token not in repr(principal)  # the secret never travels with the principal


async def test_the_database_holds_only_a_hash(engine: AsyncEngine) -> None:
    a, _, project_a, _ = await setup_tenants(engine)
    async with engine.begin() as conn:
        created = await ApiKeyRepository(conn, a).create(scopes=ALL, project_id=project_a.id)
        stored = await ApiKeyLookup(conn).find(created.stored.key_id)
    assert stored is not None
    secret = created.token.split(".", 1)[1]
    assert secret.encode() not in stored.secret_hash and secret not in repr(stored.secret_hash)


async def test_all_failure_modes_give_the_same_401(engine: AsyncEngine) -> None:
    a, _, project_a, _ = await setup_tenants(engine)
    clock = Tick()
    async with engine.begin() as conn:
        keys = ApiKeyRepository(conn, a)
        good = await keys.create(scopes=ALL, project_id=project_a.id)
        revoked = await keys.create(scopes=ALL, project_id=project_a.id)
        expiring = await keys.create(
            scopes=ALL, project_id=project_a.id, expires_at=T0 + timedelta(hours=1)
        )
        await keys.revoke(revoked.stored.key_id, T0)
    wrong_secret = good.token[: -len(good.token.split(".")[1])] + "A" * 43
    clock.now = T0 + timedelta(hours=2)  # `expiring` is now past its expiry
    attempts = [
        None,
        "",
        "garbage",
        "abb_live_" + "z" * 12 + "." + "A" * 43,  # well-formed, unknown key id
        wrong_secret,
        revoked.token,
        expiring.token,
    ]
    bodies = []
    for token in attempts:
        async with engine.begin() as conn:
            with pytest.raises(AppError) as exc:
                await authenticate(conn, token, clock)
        assert exc.value.status_code == 401 and exc.value.code == "API_KEY_INVALID"
        bodies.append((exc.value.code, exc.value.message, exc.value.details))
    assert len(set(map(repr, bodies))) == 1  # indistinguishable to the caller
    async with engine.begin() as conn:
        assert (await authenticate(conn, good.token, clock)).actor_id == f"key:{good.stored.key_id}"


async def test_expiry_is_exclusive_of_the_expiry_instant(engine: AsyncEngine) -> None:
    a, _, project_a, _ = await setup_tenants(engine)
    async with engine.begin() as conn:
        created = await ApiKeyRepository(conn, a).create(
            scopes=ALL, project_id=project_a.id, expires_at=T0 + timedelta(minutes=5)
        )
    async with engine.begin() as conn:
        await authenticate(conn, created.token, Tick(T0 + timedelta(minutes=4, seconds=59)))
    async with engine.begin() as conn:
        with pytest.raises(AppError):
            await authenticate(conn, created.token, Tick(T0 + timedelta(minutes=5)))


def test_scope_enforcement_is_a_403_with_the_missing_scope() -> None:
    import uuid

    from abb_api.authz import actions
    from abb_api.authz.principal import Principal

    principal = Principal(
        "api_key", uuid.uuid4(), None, scope_actions(frozenset({scopes.RUNS_READ})), "key:k"
    )
    authorize(principal, actions.RUN_READ)
    with pytest.raises(PermissionDenied) as exc:
        authorize(principal, actions.EVENT_WRITE)
    assert exc.value.status_code == 403 and exc.value.code == "INSUFFICIENT_SCOPE"
    # `required_scope` is what existing SDK clients read; `required_permission` is additive.
    assert exc.value.details == {
        "required_scope": "events:write",
        "required_permission": "event.write",
    }


async def test_last_used_is_throttled_to_once_a_minute(engine: AsyncEngine) -> None:
    a, _, project_a, _ = await setup_tenants(engine)
    clock = Tick()
    async with engine.begin() as conn:
        created = await ApiKeyRepository(conn, a).create(scopes=ALL, project_id=project_a.id)

    async def last_used() -> datetime | None:
        async with engine.connect() as conn:
            stored = await ApiKeyLookup(conn).find(created.stored.key_id)
        assert stored is not None
        return stored.last_used_at

    assert await last_used() is None
    async with engine.begin() as conn:
        await authenticate(conn, created.token, clock)
    assert await last_used() == T0
    clock.now = T0 + LAST_USED_RESOLUTION - timedelta(seconds=1)
    async with engine.begin() as conn:
        await authenticate(conn, created.token, clock)
    assert await last_used() == T0  # no write inside the resolution window
    clock.now = T0 + LAST_USED_RESOLUTION + timedelta(seconds=1)
    async with engine.begin() as conn:
        await authenticate(conn, created.token, clock)
    assert await last_used() == clock.now


async def test_keys_are_tenant_scoped(engine: AsyncEngine) -> None:
    a, b, project_a, project_b = await setup_tenants(engine)
    async with engine.begin() as conn:
        key_a = await ApiKeyRepository(conn, a).create(scopes=ALL, project_id=project_a.id)
        key_b = await ApiKeyRepository(conn, b).create(scopes=ALL, project_id=project_b.id)
    async with engine.begin() as conn:
        assert [k.key_id for k in await ApiKeyRepository(conn, a).list()] == [key_a.stored.key_id]
        # B cannot revoke A's key, and learns nothing about whether it exists
        assert not await ApiKeyRepository(conn, b).revoke(key_a.stored.key_id, T0)
        assert await ApiKeyRepository(conn, a).revoke(key_a.stored.key_id, T0)
        assert not await ApiKeyRepository(conn, a).revoke(key_a.stored.key_id, T0)  # already
    async with engine.begin() as conn:
        assert (await authenticate(conn, key_b.token, Tick())).workspace_id == b.workspace_id


async def test_a_key_cannot_be_bound_to_another_tenants_project_or_unknown_scopes(
    engine: AsyncEngine,
) -> None:
    a, b, project_a, _ = await setup_tenants(engine)
    async with engine.begin() as conn:
        with pytest.raises(NotFoundError):
            await ApiKeyRepository(conn, b).create(scopes=ALL, project_id=project_a.id)
        with pytest.raises(ValueError, match="unknown scopes"):
            await ApiKeyRepository(conn, a).create(scopes=frozenset({"admin:all"}))


async def test_workspace_wide_key_has_no_project(engine: AsyncEngine) -> None:
    a, *_ = await setup_tenants(engine)
    async with engine.begin() as conn:
        created = await ApiKeyRepository(conn, a).create(scopes=frozenset({scopes.RUNS_READ}))
    async with engine.begin() as conn:
        principal = await authenticate(conn, created.token, Tick())
    assert principal.project_id is None


async def test_concurrent_authentication_is_safe(engine: AsyncEngine) -> None:
    a, _, project_a, _ = await setup_tenants(engine)
    async with engine.begin() as conn:
        created = await ApiKeyRepository(conn, a).create(scopes=ALL, project_id=project_a.id)

    async def once() -> str:
        async with engine.begin() as conn:
            return (await authenticate(conn, created.token, Tick())).actor_id

    results = await asyncio.gather(*[once() for _ in range(20)])
    assert set(results) == {f"key:{created.stored.key_id}"}


@pytest.mark.parametrize(
    "token",
    [
        None,
        "garbage",
        "abb_live_" + "z" * 12 + "." + "A" * 43,  # well-formed but unknown key id
    ],
)
async def test_failed_lookups_still_pay_for_one_constant_time_comparison(
    engine: AsyncEngine, token: str | None
) -> None:
    """Unknown or malformed keys must cost the same as a wrong secret (no timing oracle)."""
    import hmac
    from unittest import mock

    spy = mock.Mock(wraps=hmac.compare_digest)
    with mock.patch.object(hmac, "compare_digest", spy):
        async with engine.begin() as conn:
            with pytest.raises(AppError):
                await authenticate(conn, token, Tick())
    assert spy.call_count == 1
