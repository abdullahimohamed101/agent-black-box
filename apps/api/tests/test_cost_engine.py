"""Pricing selection and the cost engine: versions, overrides, sources, reproducibility."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from abb_event_schema.event import Event

from abb_api.cost.builtin import BUILTIN_ENTRIES, BUILTIN_VERSION, builtin_price_book
from abb_api.cost.engine import CostEngine, cost_summary
from abb_api.cost.pricing import PriceBook, PriceEntry
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import Tenant, build_event, make_run_ids

TENANT = Tenant(
    context=TenantContext(uuid.uuid4()),
    workspace_id="ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
    projects={"p": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ"},
    project_uuids={},
)
D = Decimal


def at(day: int) -> datetime:
    return datetime(2026, 10, day, tzinfo=UTC)


def entry(version: str, pattern: str = "model-x", **kw: Any) -> PriceEntry:
    values: dict[str, Any] = {
        "pricing_version": version,
        "model_pattern": pattern,
        "valid_from": at(1),
        "input_per_million": D(3),
        "output_per_million": D(15),
    }
    values.update(kw)
    return PriceEntry(**values)


def llm(attrs: dict[str, Any], *, day: int = 7, n: int = 1, raw: bool = False) -> Event:
    """`raw` skips attribute type validation (the engine must survive what slips past)."""
    base = {"llm.provider": "example-provider", "llm.model": "model-x"}
    valid = {k: v for k, v in attrs.items() if not raw}
    event = build_event(
        TENANT,
        make_run_ids(),
        n=n,
        event_type="llm.request.completed",
        attributes={**base, **valid},
        occurred_at=f"2026-10-{day:02d}T10:00:00Z",
    )
    return event.model_copy(update={"attributes": {**base, **attrs}}) if raw else event


# ------------------------------------------------------------------ price selection


def test_entry_applies_only_inside_its_validity_window() -> None:
    e = entry("v1", valid_from=at(5), valid_to=at(10))
    assert not e.applies("p", "model-x", datetime(2026, 10, 4, 23, tzinfo=UTC))
    assert e.applies("p", "model-x", at(5))  # valid_from is inclusive
    assert not e.applies("p", "model-x", at(10))  # valid_to is exclusive


def test_model_pattern_is_case_insensitive_glob_and_provider_must_match() -> None:
    e = entry("v1", "Model-*", provider="Acme")
    assert e.applies("acme", "model-large", at(7))
    assert not e.applies("other", "model-large", at(7))
    assert not e.applies(None, "model-large", at(7))
    assert not e.applies("acme", "other-model", at(7))


def test_most_specific_entry_wins_then_the_newest() -> None:
    generic = entry("v1", "model-*", input_per_million=D(1))
    exact = entry("v1", "model-x", input_per_million=D(2))
    provider = entry("v1", "model-*", provider="p", input_per_million=D(4))
    book = PriceBook([generic, exact, provider])
    assert book.find("p", "model-x", at(7)) is provider  # a provider match outranks literal length
    assert book.find("q", "model-x", at(7)) is exact
    newer = entry("v2", "model-x", valid_from=at(6), input_per_million=D(9))
    assert PriceBook([exact, newer]).find("q", "model-x", at(7)) is newer
    assert PriceBook([exact, newer]).find("q", "model-x", at(5)) is exact  # event time decides


def test_overrides_beat_builtin_and_project_scope_beats_workspace() -> None:
    builtin = entry("2026-10-01")
    workspace = entry("override:a", origin="override", input_per_million=D(7))
    project = entry("override:b", origin="override", project_id="prj_1", input_per_million=D(8))
    book = PriceBook([builtin, workspace, project])
    assert book.find(None, "model-x", at(7)) is project
    assert PriceBook([builtin, workspace]).find(None, "model-x", at(7)) is workspace


def test_a_pinned_version_ignores_other_versions() -> None:
    old = entry("v1", input_per_million=D(3))
    new = entry("v2", valid_from=at(5), input_per_million=D(6))
    book = PriceBook([old, new])
    assert book.find(None, "model-x", at(7)) is new
    assert book.find(None, "model-x", at(7), pricing_version="v1") is old
    assert book.find(None, "model-x", at(7), pricing_version="v9") is None


# ------------------------------------------------------------------ calculation


def engine(*entries: PriceEntry) -> CostEngine:
    return CostEngine(PriceBook(entries))


def test_cost_is_tokens_times_price_per_million() -> None:
    line = engine(entry("v1")).calculate(llm({"llm.input_tokens": 8421, "llm.output_tokens": 1208}))
    assert line.source == "estimated"
    assert line.input_cost == D("0.025263") and line.output_cost == D("0.01812")
    assert line.total == D("0.043383") == line.estimated_total
    assert (line.pricing_version, line.pricing_origin) == ("v1", "builtin")


def test_cached_tokens_use_the_cached_price_and_are_not_billed_twice() -> None:
    e = entry("v1", cached_input_per_million=D("0.3"), request_price=D("0.001"))
    line = engine(e).calculate(
        llm({"llm.input_tokens": 1_000_000, "llm.cached_input_tokens": 400_000})
    )
    # 600k plain at 3 + 400k cached at 0.3 + one request
    assert line.input_cost == D("1.8") and line.cached_cost == D("0.12")
    assert line.request_cost == D("0.001") and line.total == D("1.921")
    without = engine(entry("v1")).calculate(
        llm({"llm.input_tokens": 100, "llm.cached_input_tokens": 100})
    )
    assert without.cached_cost == D("0.0003")  # no cached price: the input price
    over = engine(entry("v1")).calculate(
        llm({"llm.input_tokens": 10, "llm.cached_input_tokens": 99})
    )
    assert over.input_cost == D(0) and over.cached_cost == D("0.00003")  # clamped to input


def test_provider_reported_cost_wins_and_keeps_the_estimate_beside_it() -> None:
    line = engine(entry("v1")).calculate(
        llm({"llm.input_tokens": 1000, "cost.provider_usd": 0.5, "cost.estimated_usd": 0.4})
    )
    assert line.source == "provider_reported" and line.total == D("0.5")
    assert line.reported_total == D("0.5") and line.estimated_total == D("0.003")
    assert line.client_total == D("0.4")


def test_client_estimate_is_used_when_nothing_prices_the_model() -> None:
    line = engine().calculate(
        llm(
            {
                "llm.model": "mystery",
                "llm.input_tokens": 5,
                "cost.estimated_usd": 0.02,
                "cost.pricing_version": "client-v3",
            }
        )
    )
    assert (line.source, line.total) == ("client_estimate", D("0.02"))
    assert (line.pricing_version, line.pricing_origin) == ("client-v3", "event")


def test_an_unpriced_call_is_flagged_not_silently_zero() -> None:
    line = engine(entry("v1")).calculate(llm({"llm.model": "mystery", "llm.input_tokens": 5}))
    assert line.source == "unpriced" and line.total == D(0) and line.pricing_version is None
    no_tokens = engine(entry("v1")).calculate(llm({}))  # priced model but no usage at all
    assert no_tokens.source == "unpriced"
    summary = cost_summary([line, no_tokens])
    assert summary["unpriced_calls"] == 2 and summary["estimated_cost_usd"] == 0


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), True, "3", None])
def test_hostile_cost_values_are_ignored(bad: Any) -> None:
    line = engine().calculate(llm({"cost.estimated_usd": bad, "cost.provider_usd": bad}, raw=True))
    assert line.source == "unpriced"


def test_absurd_token_counts_are_clamped() -> None:
    line = engine(entry("v1")).calculate(llm({"llm.input_tokens": 10**30}, raw=True))
    assert line.input_tokens == 10**12
    assert line.total == D("1000000") and line.clamped  # 3,000,000 USD, clamped per call


def test_rounding_is_to_nine_places_half_even() -> None:
    line = engine(entry("v1", input_per_million=D("0.5"))).calculate(llm({"llm.input_tokens": 1}))
    assert line.input_cost == D("0.0000005")
    tiny = engine(entry("v1", input_per_million=D("0.0001"))).calculate(
        llm({"llm.input_tokens": 5})
    )
    assert tiny.input_cost == D("0")  # 5e-10 rounds half-even to 0


# ------------------------------------------------------------------ reproducibility


def test_historical_calculations_reproduce_after_prices_change() -> None:
    """Spec §79 / ADR-040: the same events priced under a pinned version give identical figures."""
    event = llm({"llm.input_tokens": 12_345, "llm.output_tokens": 678}, day=7)
    v1 = engine(*BUILTIN_ENTRIES)
    stored = v1.calculate(event)
    assert stored.pricing_version == BUILTIN_VERSION

    # Later the vendor changes prices: a new version is added; history is untouched by it.
    v2_entry = PriceEntry(
        pricing_version="2027-01-01",
        model_pattern="model-x",
        provider="example-provider",
        valid_from=datetime(2027, 1, 1, tzinfo=UTC),
        input_per_million=D("1.0"),
        output_per_million=D("5.0"),
    )
    later = CostEngine(PriceBook([*BUILTIN_ENTRIES, v2_entry]))
    assert later.calculate(event) == stored  # event time selects v1
    # A new event after the change is priced by v2; v1 can still be forced for comparison.
    new_event = llm({"llm.input_tokens": 12_345, "llm.output_tokens": 678}, day=7).model_copy(
        update={"occurred_at": datetime(2027, 2, 1, tzinfo=UTC)}
    )
    assert later.calculate(new_event).pricing_version == "2027-01-01"
    pinned = later.calculate(new_event, pricing_version=BUILTIN_VERSION)
    assert pinned.pricing_version == BUILTIN_VERSION
    assert pinned.total == stored.total  # same tokens, same pinned prices, same cost


def test_builtin_table_prices_only_example_models() -> None:
    book = builtin_price_book()
    assert book.find("example-provider", "model-x", at(7)) is not None
    mini = book.find("example-provider", "model-x-mini-2", at(7))
    assert mini is not None and mini.model_pattern == "model-x-mini*"
    assert book.find("openai", "gpt-4o", at(7)) is None  # no invented vendor prices
    assert book.find("example-provider", "model-x", datetime(2026, 9, 30, tzinfo=UTC)) is None


def test_summary_totals_are_exact_and_split_by_source() -> None:
    cheap = engine(entry("v1")).calculate(llm({"llm.input_tokens": 1_000_000}))
    retry = cheap.with_retry(True)
    summary = cost_summary([cheap, retry])
    assert summary["estimated_cost_usd"] == 6.0 and summary["retry_cost_usd"] == 3.0
    assert summary["initial_cost_usd"] == 3.0
    assert summary["cost_by_source_usd"] == {"estimated": 6.0}
