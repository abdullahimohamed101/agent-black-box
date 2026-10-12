"""Pricing selection and the cost engine: versions, overrides, sources, reproducibility."""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from abb_event_schema.event import Event

from abb_api.cost.builtin import (
    BUILTIN_ENTRIES,
    BUILTIN_VERSION,
    TIERED_ENTRIES,
    TIERED_VERSION,
    builtin_price_book,
)
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


# ------------------------------------------------------------------ prompt-size tiers


def tiered(version: str = "v1", **kw: Any) -> list[PriceEntry]:
    """A base row (2 / 10, cached 0.2) and a tier over 272,000 tokens (4 / 15, cached 0.4)."""
    base = entry(version, input_per_million=D(2), output_per_million=D(10))
    base = PriceEntry(**{**base.__dict__, "cached_input_per_million": D("0.2"), **kw})
    tier = PriceEntry(
        **{
            **base.__dict__,
            "input_per_million": D(4),
            "output_per_million": D(15),
            "cached_input_per_million": D("0.4"),
            "min_prompt_tokens": 272_000,
        }
    )
    return [base, tier]


def test_a_tier_applies_only_strictly_above_its_threshold() -> None:
    eng = engine(*tiered())
    at_edge = eng.calculate(llm({"llm.input_tokens": 272_000, "llm.output_tokens": 1_000_000}))
    over = eng.calculate(llm({"llm.input_tokens": 272_001, "llm.output_tokens": 1_000_000}))
    assert at_edge.input_cost == D("0.544") and at_edge.output_cost == D(10)
    # The whole request moves to the tier's rates, not only the tokens above the line.
    assert over.input_cost == D("1.088004") and over.output_cost == D(15)


def test_cached_tokens_are_priced_at_the_tier_cache_rate_and_count_toward_the_prompt() -> None:
    eng = engine(*tiered())
    line = eng.calculate(
        llm(
            {
                "llm.input_tokens": 300_000,
                "llm.cached_input_tokens": 250_000,
                "llm.output_tokens": 0,
            }
        )
    )
    # 50k plain at 4 + 250k cached at 0.4; the prompt (300k, cached included) is over the line.
    assert line.input_cost == D("0.2") and line.cached_cost == D("0.1")
    small = eng.calculate(
        llm(
            {
                "llm.input_tokens": 200_000,
                "llm.cached_input_tokens": 190_000,
                "llm.output_tokens": 0,
            }
        )
    )
    assert small.cached_cost == D("0.038") and small.input_cost == D("0.02")


def test_a_tier_wins_over_a_newer_base_row_and_a_model_without_tiers_stays_flat() -> None:
    base, tier = tiered()
    newer_base = PriceEntry(**{**base.__dict__, "valid_from": at(5)})
    book = PriceBook([base, tier, newer_base])
    assert book.find(None, "model-x", at(7), prompt_tokens=300_000) is tier
    assert book.find(None, "model-x", at(7), prompt_tokens=10) is newer_base
    flat = engine(entry("v1")).calculate(llm({"llm.input_tokens": 5_000_000}))
    assert flat.input_cost == D(15)  # no threshold: any prompt size, same rate


def test_the_largest_satisfied_tier_wins() -> None:
    base, tier = tiered()
    huge = PriceEntry(**{**tier.__dict__, "min_prompt_tokens": 1_000_000})
    book = PriceBook([base, tier, huge])
    assert book.find(None, "model-x", at(7), prompt_tokens=500_000) is tier
    assert book.find(None, "model-x", at(7), prompt_tokens=1_000_001) is huge


def test_an_override_without_tiers_beats_a_built_in_tier() -> None:
    override = entry("override:a", origin="override", input_per_million=D(7))
    eng = engine(*tiered(), override)
    line = eng.calculate(llm({"llm.input_tokens": 400_000}))
    assert (line.pricing_origin, line.input_cost) == ("override", D("2.8"))


def test_pinned_old_version_and_history_reproduce_with_tiers() -> None:
    old = tiered("v1")
    new_rows = [
        PriceEntry(
            **{
                **e.__dict__,
                "pricing_version": "v2",
                "valid_from": at(9),
                "input_per_million": e.input_per_million * 2,
            }
        )
        for e in old
    ]
    event = llm({"llm.input_tokens": 300_000, "llm.output_tokens": 1000}, day=7)
    before = engine(*old).calculate(event)
    after_book = engine(*old, *new_rows)
    assert after_book.calculate(event) == before  # event time selects v1 for history
    late = event.model_copy(update={"occurred_at": at(10)})
    assert after_book.calculate(late).pricing_version == "v2"
    assert after_book.calculate(late, pricing_version="v1").total == before.total
    # Recomputation is deterministic whatever the order of the entries.
    assert engine(*reversed([*old, *new_rows])).calculate(late) == after_book.calculate(late)


def test_builtin_table_has_no_invented_vendor_prices() -> None:
    book = builtin_price_book()
    assert book.find("example-provider", "model-x", at(7)) is not None
    mini = book.find("example-provider", "model-x-mini-2", at(7))
    assert mini is not None and mini.model_pattern == "model-x-mini*"
    assert book.find("openai", "gpt-4o", at(7)) is None  # no invented vendor prices
    assert book.find("example-provider", "model-x", datetime(2026, 9, 30, tzinfo=UTC)) is None


def test_builtin_anthropic_prices_match_the_recorded_source() -> None:
    book = builtin_price_book()
    sonnet = book.find("anthropic", "claude-sonnet-5-5", at(10))
    assert sonnet is not None and sonnet.source.startswith("https://platform.claude.com/docs")
    assert (sonnet.input_per_million, sonnet.output_per_million) == (D(2), D(10))
    assert sonnet.cached_input_per_million == D("0.10")
    opus = book.find("anthropic", "claude-opus-5-5-20260101", at(10))
    assert opus is not None and (opus.input_per_million, opus.output_per_million) == (D(4), D(20))
    fable = book.find("anthropic", "claude-fable-5-1", at(10))
    assert fable is not None and (fable.input_per_million, fable.output_per_million) == (
        D(10),
        D(50),
    )
    # Not priced: tiered Haiku 5.5, legacy models, other providers, events before valid_from.
    assert book.find("anthropic", "claude-haiku-5-5", at(10)) is None
    assert book.find("anthropic", "claude-haiku-4-5-20251001", at(10)) is None
    assert book.find("openai", "claude-sonnet-5-5", at(10)) is None
    assert book.find("anthropic", "claude-sonnet-5-5", at(8)) is None


def test_builtin_google_prices_follow_the_dated_and_exact_rows() -> None:
    book = builtin_price_book()
    now = book.find("google", "gemini-3.8-flash", at(10))
    assert now is not None and (now.input_per_million, now.output_per_million) == (
        D("0.75"),
        D("3.75"),
    )
    after = book.find("google", "gemini-3.8-flash", datetime(2027, 1, 1, tzinfo=UTC))
    assert after is not None and (after.input_per_million, after.output_per_million) == (
        D("1.50"),
        D("7.50"),
    )
    lite = book.find("google", "gemini-3.5-flash-lite", at(10))
    assert lite is not None and lite.cached_input_per_million is None
    assert book.find("google", "gemini-3.5-flash", at(10)) is not lite
    # Exact IDs only: a different model sharing the prefix must stay unpriced.
    assert book.find("google", "gemini-3.8-flash-tts", at(10)) is None
    assert book.find("google", "gemini-3.1-pro-preview", at(10)) is None
    assert book.find("anthropic", "gemini-3.5-flash", at(10)) is None


def test_every_vendor_entry_records_its_source_and_fetch_date() -> None:
    vendor = [e for e in BUILTIN_ENTRIES if e.provider != "example-provider"]
    assert len(vendor) > 40
    for e in vendor:
        assert e.source.startswith("https://"), e.model_pattern
        assert re.search(r"read (twice )?20\d\d-\d\d-\d\d", e.source), e.model_pattern
    for e in TIERED_ENTRIES:
        assert e.pricing_version == TIERED_VERSION and e.valid_from == datetime(
            2026, 10, 12, tzinfo=UTC
        )
        assert e.currency == "USD" and e.origin == "builtin"
    promo = book_entry("openai", "gpt-5.6-sol", 12)
    assert "2026-11-21" in promo.source


def book_entry(provider: str, model: str, day: int, prompt: int = 0) -> PriceEntry:
    found = builtin_price_book().find(provider, model, at(day), prompt_tokens=prompt)
    assert found is not None, model
    return found


def test_builtin_openai_tiers_use_the_272k_rule_for_the_whole_request() -> None:
    short = book_entry("openai", "gpt-6-astra", 12, 272_000)
    long_ = book_entry("openai", "gpt-6-astra", 12, 272_001)
    assert (short.input_per_million, short.cached_input_per_million, short.output_per_million) == (
        D(10),
        D(1),
        D(50),
    )
    assert (long_.input_per_million, long_.cached_input_per_million, long_.output_per_million) == (
        D(20),
        D(2),
        D(75),
    )
    assert long_.min_prompt_tokens == 272_000
    event = llm(
        {
            "llm.provider": "openai",
            "llm.model": "gpt-6-astra",
            "llm.input_tokens": 300_000,
            "llm.cached_input_tokens": 100_000,
            "llm.output_tokens": 10_000,
        },
        day=13,
    )
    line = CostEngine(builtin_price_book()).calculate(event)
    # 200k plain at 20 + 100k cached at 2 + 10k out at 75, all at the long-context rates.
    assert (line.input_cost, line.cached_cost, line.output_cost) == (D(4), D("0.2"), D("0.75"))
    # The 2026-10-12b batch re-publishes the standard rows; the later version wins the tie, and a
    # pin to the earlier version still reproduces it with the same figures.
    assert line.pricing_version == "2026-10-12b"
    pinned = CostEngine(builtin_price_book()).calculate(event, pricing_version=TIERED_VERSION)
    assert pinned.pricing_version == TIERED_VERSION and pinned.total == line.total


def test_builtin_models_without_a_long_tier_stay_flat_and_old_dates_stay_unpriced() -> None:
    codex = book_entry("openai", "gpt-5.3-codex", 12, 900_000)
    assert codex.min_prompt_tokens is None and codex.input_per_million == D("1.75")
    pro = book_entry("openai", "gpt-5.2-pro", 12, 900_000)
    assert pro.cached_input_per_million is None and pro.input_per_million == D(21)
    book = builtin_price_book()
    assert book.find("openai", "gpt-6-astra", at(11), prompt_tokens=10) is None  # before valid_from
    # Left out on purpose: restricted, moving alias, and a model page that states no long tier.
    for model in ("chat-latest", "gpt-5.6-cyber", "gpt-rosalind-research", "gpt-5.5-pro"):
        assert book.find("openai", model, at(13), prompt_tokens=10) is None
    assert book.find("openai", "gpt-4o-2024-08-06", at(13)) is None  # exact IDs only


def test_builtin_haiku_and_gemini_pro_tiers() -> None:
    low = book_entry("anthropic", "claude-haiku-5-5", 12, 100_000)
    high = book_entry("anthropic", "claude-haiku-5-5-20261001", 12, 100_001)
    assert (low.input_per_million, low.cached_input_per_million, low.output_per_million) == (
        D("0.10"),
        D("0.01"),
        D("0.50"),
    )
    assert (high.input_per_million, high.cached_input_per_million, high.output_per_million) == (
        D("0.50"),
        D("0.05"),
        D("2.50"),
    )
    pro = book_entry("google", "gemini-3.1-pro-preview", 12, 200_001)
    assert (pro.input_per_million, pro.cached_input_per_million, pro.output_per_million) == (
        D(4),
        D("0.40"),
        D(18),
    )
    assert book_entry("google", "gemini-2.5-pro", 12, 200_000).input_per_million == D("1.25")
    # Gemini Flash models priced by modality came in 2026-10-12b (text price only).
    assert builtin_price_book().find("google", "gemini-2.5-flash", at(13)) is not None
    assert builtin_price_book().find("google", "gemini-2.5-flash-8b", at(13)) is None


def test_a_workspace_override_beats_a_builtin_tier_end_to_end() -> None:
    override = entry(
        "override:x",
        "gpt-6-astra",
        provider="openai",
        origin="override",
        valid_from=at(1),
        input_per_million=D(1),
        output_per_million=D(1),
    )
    book = PriceBook([*BUILTIN_ENTRIES, override])
    assert book.find("openai", "gpt-6-astra", at(13), prompt_tokens=500_000) is override


def test_summary_totals_are_exact_and_split_by_source() -> None:
    cheap = engine(entry("v1")).calculate(llm({"llm.input_tokens": 1_000_000}))
    retry = cheap.with_retry(True)
    summary = cost_summary([cheap, retry])
    assert summary["estimated_cost_usd"] == 6.0 and summary["retry_cost_usd"] == 3.0
    assert summary["initial_cost_usd"] == 3.0
    assert summary["cost_by_source_usd"] == {"estimated": 6.0}
