"""`GET /v1/pricing` and the pricing CLI: visibility per key, bad input, rebuild of past runs."""

from datetime import UTC, datetime
from decimal import Decimal

from abb_event_schema.ids import to_uuid
from sqlalchemy import select

from abb_api.cost.repository import CostRepository
from abb_api.db import tables as t
from tests.api_fixtures import Api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
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
