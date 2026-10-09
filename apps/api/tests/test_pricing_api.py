"""`GET /v1/pricing` and the pricing CLI: visibility per key, bad input, rebuild of past runs."""

from datetime import UTC, datetime
from decimal import Decimal

from abb_event_schema.ids import IdKind, to_uuid
from sqlalchemy import select, text

from abb_api.cost.repository import CostRepository
from abb_api.db import tables as t
from abb_api.ids import new_uuid, public_id
from tests.api_fixtures import Api, person
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.test_audit import rows
from tests.test_cli import invoke

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def add_override(api: Api, tenant_ctx, project_id=None, pattern: str = "mine*") -> None:  # type: ignore[no-untyped-def]
    async with api.engine.begin() as conn:
        await CostRepository(conn, tenant_ctx).add_override(
            project_id=project_id, provider=None, model_pattern=pattern,
            input_per_million=Decimal(2), output_per_million=Decimal(4),
            cached_input_per_million=None, request_price=Decimal(0),
            valid_from=EPOCH, note=None,
        )  # fmt: skip


async def test_prices_list_builtin_entries_and_scoped_overrides(api: Api) -> None:
    await add_override(api, api.tenant.context, pattern="ws-wide*")
    await add_override(api, api.tenant.context, api.tenant.project_uuids["beta"], "beta-only*")
    await add_override(api, api.other.context, pattern="globex*")

    body = (await api.get("/v1/pricing", token="wide_reader")).json()["prices"]
    patterns = {p["model_pattern"] for p in body}
    assert patterns >= {"model-x", "ws-wide*", "beta-only*"} and "globex*" not in patterns
    assert {p["origin"] for p in body} == {"builtin", "override"}
    assert next(p for p in body if p["model_pattern"] == "model-x")["input_per_million"] == "3.0"

    # an alpha-bound key sees workspace-wide overrides but not beta's
    mine = {p["model_pattern"] for p in (await api.get("/v1/pricing")).json()["prices"]}
    assert "ws-wide*" in mine and "beta-only*" not in mine


async def test_pricing_access_rules(api: Api) -> None:
    assert (await api.get("/v1/pricing", token=None)).status_code == 401
    assert (await api.get("/v1/pricing", token="ingest_only")).status_code == 403
    beta = api.tenant.projects["beta"]
    assert (await api.get("/v1/pricing", project_id=beta)).status_code == 404  # alpha key probing
    assert (await api.get("/v1/pricing", token="wide_reader", project_id=beta)).status_code == 200
    foreign = api.other.projects["p"]
    assert (
        await api.get("/v1/pricing", token="wide_reader", project_id=foreign)
    ).status_code == 404
    assert (await api.get("/v1/pricing", project_id="nonsense")).status_code == 422


async def test_cli_override_then_rebuild_reprices_a_finished_run(
    api: Api, database_url: str
) -> None:
    settings = make_settings(database_url)
    run = make_run_ids()
    call = wire_event(
        run, 1, event_type="llm.request.completed",
        attributes={"llm.provider": "p", "llm.model": "mine-1", "llm.input_tokens": 1_000_000},
    )  # fmt: skip
    assert (await api.post_batch([call])).status_code == 202
    await api.drain()

    async def total() -> Decimal:
        async with api.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(t.cost_calculations).where(
                        t.cost_calculations.c.run_id == to_uuid(run["run_id"])
                    )
                )
            ).one()
        return row.total  # type: ignore[no-any-return]

    assert await total() == Decimal(0)  # unpriced before the override

    code, _, err = await invoke(
        settings, "set-pricing-override", "--workspace", "acme", "--model-pattern", "mine*",
        "--input-per-million", "2", "--output-per-million", "4", "--note", "negotiated",
    )  # fmt: skip
    assert code == 0 and "override:" in err
    code, _, err = await invoke(settings, "rebuild-costs", "--workspace", "acme")
    assert code == 0 and "queued 1 of 1" in err
    await api.drain()
    assert await total() == Decimal(2)
    code, out, _ = await invoke(settings, "list-pricing", "--workspace", "acme")
    assert code == 0 and "mine*" in out and "model-x" in out


async def test_cli_rejects_bad_prices(api: Api, database_url: str) -> None:
    settings = make_settings(database_url)
    for bad in ("-1", "nan", "abc", "inf"):
        try:
            code, _, _ = await invoke(
                settings, "set-pricing-override", "--workspace", "acme", "--model-pattern", "x",
                "--input-per-million", bad, "--output-per-million", "1",
            )  # fmt: skip
        except SystemExit as exit_:  # argparse reports bad arguments by exiting with 2
            code = int(str(exit_.code))
        assert code == 2


async def test_the_newer_of_two_equal_overrides_wins_every_time(api: Api) -> None:
    """Same pattern and valid_from: the later row used to lose half the time (random id order)."""
    from abb_api.cost.pricing import PriceBook

    wrong = 0
    for _ in range(100):
        async with api.engine.begin() as conn:
            repo = CostRepository(conn, api.tenant.context)
            for price in (1, 2):  # the second is the correction
                await repo.add_override(
                    project_id=None, provider=None, model_pattern="dup*",
                    input_per_million=Decimal(price), output_per_million=Decimal(price),
                    cached_input_per_million=None, request_price=Decimal(0),
                    valid_from=EPOCH, note=None,
                )  # fmt: skip
            book = PriceBook([r.entry() for r in await repo.overrides()])
            entry = book.find(None, "dup-1", datetime(2026, 10, 7, tzinfo=UTC))
            wrong += entry is None or entry.input_per_million != Decimal(2)
            await conn.execute(t.pricing_overrides.delete())
    assert wrong == 0


# ------------------------------------------------ writing prices through the API (KI-051)


async def run_total(api: Api, run: dict[str, str]) -> Decimal:
    async with api.engine.connect() as conn:
        row = (
            await conn.execute(
                select(t.cost_calculations).where(
                    t.cost_calculations.c.run_id == to_uuid(run["run_id"])
                )
            )
        ).one()
    return row.total  # type: ignore[no-any-return]


async def ingest_priced_call(
    api: Api, model: str = "mine-1", tokens: int = 1_000_000
) -> dict[str, str]:
    run = make_run_ids()
    call = wire_event(
        run, 1, event_type="llm.request.completed",
        attributes={"llm.provider": "p", "llm.model": model, "llm.input_tokens": tokens},
    )  # fmt: skip
    assert (await api.post_batch([call])).status_code == 202
    await api.drain()
    return run


OVERRIDE = {"model_pattern": "mine*", "input_per_million": "2", "output_per_million": 4}


async def test_an_override_and_a_rebuild_reprice_a_finished_run(web: Api) -> None:
    run = await ingest_priced_call(web)
    assert await run_total(web, run) == Decimal(0)
    billing = await person(web, "b@acme.test", "BILLING")

    created = await web.client.post(
        "/v1/pricing/overrides", json={**OVERRIDE, "note": "negotiated"}, headers=billing
    )
    assert created.status_code == 201, created.text
    price = created.json()
    assert price["origin"] == "override" and price["model_pattern"] == "mine*"
    assert price["input_per_million"] == "2.000000000" or Decimal(price["input_per_million"]) == 2
    assert price["project_id"] is None and price["pricing_version"].startswith("override:")
    listed = (await web.get("/v1/pricing", token="wide_reader")).json()["prices"]
    assert price["pricing_version"] in {p["pricing_version"] for p in listed}

    rebuilt = await web.client.post("/v1/cost/rebuild", json={}, headers=billing)
    assert rebuilt.status_code == 202
    assert rebuilt.json() == {"matched": 1, "queued": 1, "truncated": False}
    again = await web.client.post("/v1/cost/rebuild", json={}, headers=billing)
    assert again.json()["queued"] == 0  # already queued: the job is deduplicated
    await web.drain()
    assert await run_total(web, run) == Decimal(2)

    found = [(r.action, r.actor_kind) for r in await rows(web)]
    assert found.count(("pricing_override.create", "user")) == 1
    assert found.count(("cost.rebuild", "user")) == 2


async def test_only_owner_admin_and_billing_write_prices(web: Api) -> None:
    for email, role in (
        ("d@acme.test", "DEVELOPER"),
        ("v@acme.test", "VIEWER"),
        ("s@acme.test", "SECURITY"),
    ):
        headers = await person(web, email, role)
        for path, body in (("/v1/pricing/overrides", OVERRIDE), ("/v1/cost/rebuild", {})):
            denied = await web.client.post(path, json=body, headers=headers)
            assert denied.status_code == 403, (role, path)
            assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    for token in ("writer", "wide_reader", "wide"):
        got = await web.client.post(
            "/v1/pricing/overrides", json=OVERRIDE, headers=web.headers(token)
        )
        assert got.status_code == 403 and got.json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    async with web.engine.connect() as conn:
        assert (await conn.execute(select(t.pricing_overrides))).first() is None
    assert [r.action for r in await rows(web) if r.outcome == "allowed"] == []


async def test_overrides_are_validated(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    base = dict(OVERRIDE)
    bad_bodies: list[dict[str, object]] = [
        {**base, "input_per_million": "-1"},
        {**base, "input_per_million": "NaN"},
        {**base, "input_per_million": "Infinity"},
        {**base, "input_per_million": "abc"},
        {**base, "input_per_million": "1000001"},
        {**base, "input_per_million": "1.0000000001"},
        {**base, "output_per_million": None},
        {**base, "request_price": -0.1},
        {**base, "cached_input_per_million": -1},
        {**base, "model_pattern": ""},
        {**base, "model_pattern": "x" * 129},
        {**base, "model_pattern": "bad\x00"},
        {**base, "provider": ""},
        {**base, "note": "x" * 501},
        {**base, "valid_from": "2026-10-01T00:00:00"},  # no timezone
        {**base, "valid_from": "1969-12-31T00:00:00Z"},
        {**base, "valid_from": "2101-01-01T00:00:00Z"},
        {**base, "project_id": "nonsense"},
    ]
    for body in bad_bodies:
        got = await web.client.post("/v1/pricing/overrides", json=body, headers=owner)
        assert got.status_code == 422, body
    ok = await web.client.post(
        "/v1/pricing/overrides",
        json={
            **base,
            "valid_from": "2026-10-01T00:00:00Z",
            "provider": "p",
            "request_price": "0.01",
        },
        headers=owner,
    )
    assert ok.status_code == 201 and ok.json()["valid_from"].startswith("2026-10-01T00:00:00")
    assert Decimal(ok.json()["request_price"]) == Decimal("0.01")


async def test_a_project_override_needs_a_project_of_this_workspace(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    beta = web.tenant.projects["beta"]
    scoped = await web.client.post(
        "/v1/pricing/overrides", json={**OVERRIDE, "project_id": beta}, headers=owner
    )
    assert scoped.status_code == 201 and scoped.json()["project_id"] == beta
    foreign = await web.client.post(
        "/v1/pricing/overrides",
        json={**OVERRIDE, "project_id": web.other.projects["p"]},
        headers=owner,
    )
    random = await web.client.post(
        "/v1/pricing/overrides",
        json={**OVERRIDE, "project_id": public_id(IdKind.PROJECT, new_uuid(IdKind.PROJECT))},
        headers=owner,
    )
    assert foreign.status_code == random.status_code == 404
    assert foreign.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    assert foreign.json()["error"]["message"] == random.json()["error"]["message"]
    for path, body in (("/v1/cost/rebuild", {"project_id": web.other.projects["p"]}),):
        denied = await web.client.post(path, json=body, headers=owner)
        assert denied.status_code == 404 and denied.json()["error"]["code"] == "PROJECT_NOT_FOUND"


async def test_the_override_bound_is_enforced(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    async with web.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO pricing_overrides (workspace_id, id, model_pattern, "
                "input_per_million, output_per_million, valid_from) "
                "SELECT :w, gen_random_uuid(), 'm' || n, 1, 1, now() "
                "FROM generate_series(1, 999) n"
            ),
            {"w": web.tenant.context.workspace_id},
        )
    assert (
        await web.client.post("/v1/pricing/overrides", json=OVERRIDE, headers=owner)
    ).status_code == 201
    over = await web.client.post("/v1/pricing/overrides", json=OVERRIDE, headers=owner)
    assert over.status_code == 409 and over.json()["error"]["code"] == "LIMIT_REACHED"


async def test_rebuild_is_bounded_scoped_and_stays_in_the_workspace(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    runs = [await ingest_priced_call(web) for _ in range(3)]
    foreign_run = make_run_ids()
    foreign = wire_event(
        foreign_run, 1, event_type="llm.request.completed",
        attributes={"llm.provider": "p", "llm.model": "mine-1", "llm.input_tokens": 10},
    )  # fmt: skip
    assert (await web.post_batch([foreign], token="other")).status_code == 202
    await web.drain()

    limited = await web.client.post("/v1/cost/rebuild", json={"limit": 2}, headers=owner)
    assert limited.json() == {"matched": 2, "queued": 2, "truncated": True}
    await web.drain()
    for bad in (
        {"limit": 0},
        {"limit": 10_001},
        {"since": "2026-10-01T00:00:00"},
        {"project_id": "x"},
    ):
        assert (
            await web.client.post("/v1/cost/rebuild", json=bad, headers=owner)
        ).status_code == 422
    future = await web.client.post(
        "/v1/cost/rebuild", json={"since": "2100-01-01T00:00:00Z"}, headers=owner
    )
    assert future.json() == {"matched": 0, "queued": 0, "truncated": False}
    alpha = await web.client.post(
        "/v1/cost/rebuild", json={"project_id": web.tenant.projects["beta"]}, headers=owner
    )
    assert alpha.json()["matched"] == 0  # beta has no runs
    everything = await web.client.post("/v1/cost/rebuild", json={}, headers=owner)
    assert everything.json()["matched"] == len(runs)  # globex's run is not acme's to rebuild
    async with web.engine.connect() as conn:
        queued = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM outbox_jobs "
                    "WHERE workspace_id = :w AND status = 'pending'"
                ),
                {"w": web.other.context.workspace_id},
            )
        ).scalar_one()
    assert queued == 0
