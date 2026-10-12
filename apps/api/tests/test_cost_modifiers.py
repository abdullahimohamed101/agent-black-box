"""Billing modifiers (ADR-044): service tiers, regional endpoints, cache writes, batch-2 data.

Prices are the vendors' own (docs/research/openai-and-tiered-prices-2026-10-12.md). A modifier a
vendor page does not price leaves the call unpriced with a reason, never at the standard rate."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from abb_api.cost.builtin import (
    BATCH2_ENTRIES,
    BATCH2_VERSION,
    BUILTIN_ENTRIES,
    TIERED_ENTRIES,
    TIERED_VERSION,
    builtin_price_book,
)
from abb_api.cost.engine import (
    NO_PRICE,
    NO_PRICE_FOR_TIER,
    NO_REGIONAL_PRICE,
    UNKNOWN_TIER,
    CostEngine,
    CostLine,
)
from abb_api.cost.pricing import KNOWN_TIERS, PriceBook
from tests.test_cost_engine import D, at, entry, llm

BOOK = builtin_price_book()
ENGINE = CostEngine(BOOK)
M = 1_000_000


def call(
    provider: str,
    model: str,
    *,
    tokens_in: int = M,
    tokens_out: int = 0,
    engine: CostEngine = ENGINE,
    **attrs: Any,
) -> CostLine:
    event = llm(
        {
            "llm.provider": provider,
            "llm.model": model,
            "llm.input_tokens": tokens_in,
            "llm.output_tokens": tokens_out,
            **attrs,
        },
        day=13,
    )
    return engine.calculate(event)


def total(provider: str, model: str, **kw: Any) -> Decimal:
    line = call(provider, model, **kw)
    assert line.source == "estimated", (model, line.unpriced_reason)
    return line.total


# ------------------------------------------------------------------ the engine on a tiny book


def small_book(**kw: Any) -> CostEngine:
    base = {"provider": "acme", "valid_from": at(1)}
    rows = [
        entry("v", "m", input_per_million=D(2), output_per_million=D(10), **base, **kw),
        entry(
            "v",
            "m",
            service_tier="batch",
            input_per_million=D(1),
            output_per_million=D(5),
            **base,
        ),
    ]
    return CostEngine(PriceBook(rows))


def test_a_known_tier_selects_its_own_row_and_default_is_standard() -> None:
    engine = small_book()
    assert call("acme", "m", tokens_out=M, engine=engine).total == D(12)
    assert call(
        "acme", "m", tokens_out=M, engine=engine, **{"llm.service_tier": "batch"}
    ).total == D(6)
    assert call(
        "acme", "m", tokens_out=M, engine=engine, **{"llm.service_tier": " BATCH "}
    ).total == D(6)


def test_an_unknown_tier_is_unpriced_with_a_reason_never_standard() -> None:
    line = call("acme", "m", engine=small_book(), **{"llm.service_tier": "scale"})
    assert (line.source, line.total, line.unpriced_reason) == ("unpriced", 0, UNKNOWN_TIER)
    assert line.estimated_total is None


def test_other_sources_still_win_over_an_unpriced_tier() -> None:
    engine = small_book()
    reported = call(
        "acme", "m", engine=engine, **{"llm.service_tier": "scale", "cost.provider_usd": 0.5}
    )
    assert (reported.source, reported.total) == ("provider_reported", D("0.5"))
    estimate = call(
        "acme", "m", engine=engine, **{"llm.service_tier": "scale", "cost.estimated_usd": 0.25}
    )
    assert (estimate.source, estimate.total) == ("client_estimate", D("0.25"))


def test_a_tier_the_model_has_no_row_for_is_unpriced_with_its_reason() -> None:
    line = call("acme", "m", engine=small_book(), **{"llm.service_tier": "fast"})
    assert (line.source, line.unpriced_reason) == ("unpriced", NO_PRICE_FOR_TIER)
    other = call("acme", "nothing", engine=small_book())
    assert (other.source, other.unpriced_reason) == ("unpriced", NO_PRICE)


def test_an_override_prices_every_tier_and_is_never_scaled_for_a_region() -> None:
    override = entry(
        "override:1",
        "m",
        provider="acme",
        origin="override",
        service_tier=None,
        input_per_million=D(7),
        output_per_million=D(7),
    )
    engine = CostEngine(PriceBook([override, *small_book()._prices.entries]))
    for tier in ("standard", "batch", "fast"):
        line = call("acme", "m", engine=engine, **{"llm.service_tier": tier})
        assert (line.total, line.pricing_origin) == (D(7), "override")
    regional = call("acme", "m", engine=engine, **{"llm.data_residency": "eu"})
    assert regional.total == D(7)


def test_cache_writes_are_billed_at_their_price_or_the_input_price() -> None:
    priced = small_book(cache_write_per_million=D(4), cache_write_1h_per_million=D(6))
    # 1M input: 100k read from cache (free here), 300k written (100k of them 1h), 600k plain.
    attrs = {
        "llm.cached_input_tokens": 100_000,
        "llm.cache_write_tokens": 300_000,
        "llm.cache_write_1h_tokens": 100_000,
    }
    line = call("acme", "m", engine=priced, **attrs)
    # plain 0.6M*2 + 5m 0.2M*4 + 1h 0.1M*6, cached at the input price 0.1M*2
    assert line.input_cost == D("1.2") + D("0.8") + D("0.6")
    assert line.cached_cost == D("0.2")
    no_price = call("acme", "m", engine=small_book(), **attrs)
    assert no_price.input_cost == D("2") * D("0.9")  # writes cost the input price
    no_1h = small_book(cache_write_per_million=D(4))
    assert call("acme", "m", engine=no_1h, **attrs).input_cost == D("1.2") + D("1.2")


def test_cache_write_counts_are_bounded_by_the_prompt() -> None:
    engine = small_book(cache_write_per_million=D(4), cache_write_1h_per_million=D(6))
    line = call(
        "acme",
        "m",
        tokens_in=100,
        engine=engine,
        **{
            "llm.cached_input_tokens": 60,
            "llm.cache_write_tokens": 10**9,  # hostile: more than the prompt holds
            "llm.cache_write_1h_tokens": 10**12,
        },
    )
    assert line.input_cost == D("40") * D(6) / M  # at most the remaining 40 tokens, all 1h
    assert line.cached_cost == D("60") * D(2) / M


def test_a_regional_call_needs_a_stated_regional_price() -> None:
    engine = small_book(regional_multiplier=D("1.1"))
    assert call("acme", "m", engine=engine, **{"llm.data_residency": "us"}).total == D("2.2")
    assert call("acme", "m", engine=engine, **{"llm.data_residency": "global"}).total == D(2)
    batch = call(
        "acme", "m", engine=engine, **{"llm.data_residency": "us", "llm.service_tier": "batch"}
    )
    assert (batch.source, batch.unpriced_reason) == ("unpriced", NO_REGIONAL_PRICE)
    plain = call("acme", "m", engine=small_book(), **{"llm.data_residency": "us"})
    assert plain.unpriced_reason == NO_REGIONAL_PRICE


def test_a_prompt_above_a_ceiling_is_unpriced_not_charged_the_small_rates() -> None:
    book = PriceBook(
        [entry("v", "m", provider="acme", max_prompt_tokens=1000, service_tier="fast")]
    )
    engine = CostEngine(book)
    ok = call("acme", "m", tokens_in=1000, engine=engine, **{"llm.service_tier": "fast"})
    over = call("acme", "m", tokens_in=1001, engine=engine, **{"llm.service_tier": "fast"})
    assert ok.source == "estimated"
    assert (over.source, over.unpriced_reason) == ("unpriced", NO_PRICE_FOR_TIER)


def test_an_event_without_the_new_attributes_is_priced_exactly_as_before() -> None:
    old = CostEngine(PriceBook([entry("v", "m", provider="acme", cached_input_per_million=D(1))]))
    line = call(
        "acme", "m", tokens_in=M, tokens_out=M, engine=old, **{"llm.cached_input_tokens": 400_000}
    )
    assert (line.input_cost, line.cached_cost, line.output_cost) == (D("1.8"), D("0.4"), D(15))
    assert line.unpriced_reason is None


# ------------------------------------------------------------------ the batch-2 data


def test_anthropic_modifiers_follow_the_pages() -> None:
    # Claude Opus 5.5: 4 in / 20 out; Batch halves, Fast doubles, US-only inference is 1.1x.
    kw = {"tokens_out": M}
    std = total("anthropic", "claude-opus-5-5", **kw)
    assert std == D(24)
    assert total("anthropic", "claude-opus-5-5", **kw, **{"llm.service_tier": "batch"}) == D(12)
    assert total("anthropic", "claude-opus-5-5", **kw, **{"llm.service_tier": "fast"}) == D(48)
    us = {"llm.data_residency": "us"}
    assert total("anthropic", "claude-opus-5-5", **kw, **us) == D("26.4")
    assert total("anthropic", "claude-opus-5-5", **kw, **us, **{"llm.service_tier": "batch"}) == D(
        "13.2"
    )
    assert total("anthropic", "claude-opus-5-5", **kw, **us, **{"llm.service_tier": "fast"}) == D(
        "52.8"
    )


def test_anthropic_cache_writes_stack_with_batch_fast_and_region() -> None:
    # Opus 5.5 cache writes: 5m = 1.25 x 4 = 5, 1h = 2 x 4 = 8 per million.
    writes = {"llm.cache_write_tokens": M, "llm.cache_write_1h_tokens": 400_000}
    base = 600_000 * D(5) / M + 400_000 * D(8) / M
    assert call("anthropic", "claude-opus-5-5", **writes).total == base
    assert (
        call("anthropic", "claude-opus-5-5", **writes, **{"llm.service_tier": "batch"}).total
        == base / 2
    )
    assert (
        call("anthropic", "claude-opus-5-5", **writes, **{"llm.service_tier": "fast"}).total
        == base * 2
    )
    assert call(
        "anthropic", "claude-opus-5-5", **writes, **{"llm.data_residency": "us"}
    ).total == base * D("1.1")
    # Cache reads on Opus 5.5 are 0.05x; Fast mode applies on top (page: caching multipliers
    # apply on top of fast mode pricing).
    read = {"llm.cached_input_tokens": M}
    assert total("anthropic", "claude-opus-5-5", **read) == D("0.20")
    assert total("anthropic", "claude-opus-5-5", **read, **{"llm.service_tier": "fast"}) == D(
        "0.40"
    )


def test_fast_mode_exists_only_for_the_three_opus_models_the_page_lists() -> None:
    for model in ("claude-opus-5", "claude-opus-4-8", "claude-opus-5-5-20261001"):
        assert total("anthropic", model, **{"llm.service_tier": "fast"}) == 2 * total(
            "anthropic", model
        )
    for model in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5-5", "claude-haiku-5-5"):
        line = call("anthropic", model, **{"llm.service_tier": "fast"})
        assert (line.source, line.unpriced_reason) == ("unpriced", NO_PRICE_FOR_TIER), model


def test_anthropic_priority_tier_and_old_models_in_a_region_are_unpriced() -> None:
    assert (
        call("anthropic", "claude-sonnet-5-5", **{"llm.service_tier": "priority"}).unpriced_reason
        == UNKNOWN_TIER
    )
    for model in ("claude-haiku-4-5", "claude-opus-4-5-20251101", "claude-sonnet-4-5"):
        line = call("anthropic", model, **{"llm.data_residency": "us"})
        assert line.unpriced_reason == NO_REGIONAL_PRICE, model
        assert total("anthropic", model, **{"llm.data_residency": "global"}) > 0


def test_haiku_tier_boundary_combines_with_batch_and_region() -> None:
    def haiku(prompt: int, **attrs: Any) -> Decimal:
        return total("anthropic", "claude-haiku-5-5", tokens_in=prompt, tokens_out=M, **attrs)

    batch = {"llm.service_tier": "batch"}
    us = {"llm.data_residency": "us"}
    assert haiku(100_000) == D("0.1") * D("0.1") + D("0.5")  # 100k in at 0.10, 1M out at 0.50
    assert haiku(100_001) == D("100001") * D("0.5") / M + D("2.5")
    assert haiku(100_000, **batch) == D("0.005") + D("0.25")
    assert haiku(100_001, **batch) == D("100001") * D("0.25") / M + D("1.25")
    assert haiku(100_001, **batch, **us) == (D("100001") * D("0.25") / M + D("1.25")) * D("1.1")


def test_openai_tiers_use_the_rows_the_pricing_page_states() -> None:
    # 100k in, 100k out, below the 272K long-context line. gpt-6.1-sol: 2 / 0.10 / 2.50 / 10
    # standard; Batch = Flex = 1 / 0.05 / 1.25 / 5; Fast 4 / ...; Ultrafast 12 / ... / 60.
    kw = {"tokens_in": 100_000, "tokens_out": 100_000}
    assert total("openai", "gpt-6.1-sol", **kw) == D("1.2")
    assert total("openai", "gpt-6.1-sol", **kw, **{"llm.service_tier": "batch"}) == D("0.6")
    assert total("openai", "gpt-6.1-sol", **kw, **{"llm.service_tier": "flex"}) == D("0.6")
    assert total("openai", "gpt-6.1-sol", **kw, **{"llm.service_tier": "fast"}) == D("2.4")
    assert total("openai", "gpt-6.1-sol", **kw, **{"llm.service_tier": "ultrafast"}) == D("7.2")
    # Fast is not a flat 2x everywhere: gpt-5.5 is 2.5x, gpt-4.1 is 1.75x.
    fast = {"tokens_in": 100_000, "llm.service_tier": "fast"}
    assert total("openai", "gpt-5.5", **fast) == D("1.25")
    assert total("openai", "gpt-4.1", **fast) == D("0.35")
    assert total("openai", "gpt-5.3-codex", **fast) == D("0.35")
    # Batch has no cached price on gpt-4.1: cached tokens cost the batch input price.
    batch = {"tokens_in": 100_000, "llm.service_tier": "batch", "llm.cached_input_tokens": 100_000}
    assert total("openai", "gpt-4.1", **batch) == D("0.1")


def test_openai_long_context_applies_inside_a_tier_and_a_missing_one_is_unpriced() -> None:
    fast = {"llm.service_tier": "fast"}
    assert total("openai", "gpt-6-astra", tokens_in=272_000, **fast) == D("272000") * D(20) / M
    assert total("openai", "gpt-6-astra", tokens_in=272_001, **fast) == D("272001") * D(40) / M
    # gpt-5.5 Fast has no long-context price ("-"): over 272K nothing is stated.
    assert total("openai", "gpt-5.5", tokens_in=272_000, **fast) == D("272000") * D("12.5") / M
    over = call("openai", "gpt-5.5", tokens_in=272_001, **fast)
    assert (over.source, over.unpriced_reason) == ("unpriced", NO_PRICE_FOR_TIER)
    # gpt-5.4-pro Batch states a long price, so it is priced.
    assert (
        total("openai", "gpt-5.4-pro", tokens_in=272_001, **{"llm.service_tier": "batch"})
        == D("272001") * D(30) / M
    )


def test_openai_cache_writes_cost_their_column_and_models_without_one_cost_input() -> None:
    writes = {"tokens_in": 100_000, "llm.cache_write_tokens": 40_000}
    # gpt-6.1-sol: 2 per million input, 2.50 for writes.
    assert total("openai", "gpt-6.1-sol", **writes) == D("0.06") * 2 + D("0.04") * D("2.5")
    batch = {**writes, "llm.service_tier": "batch"}
    assert total("openai", "gpt-6.1-sol", **batch) == D("0.06") * 1 + D("0.04") * D("1.25")
    # gpt-5.5 lists no cache-write column ("-"): written tokens are plain input.
    assert total("openai", "gpt-5.5", **writes) == D("0.5")


def test_openai_regional_uplift_only_where_a_model_page_states_it() -> None:
    eu = {"tokens_in": 100_000, "llm.data_residency": "eu"}
    assert total("openai", "gpt-5.4", **eu) == D("0.25") * D("1.1")
    assert total("openai", "gpt-6.1-sol", **eu) == D("0.22")
    assert total(
        "openai", "gpt-6.1-sol", tokens_in=100_000, **{"llm.data_residency": "global"}
    ) == D("0.2")
    for model in ("gpt-6-astra", "gpt-5.6-terra", "gpt-5.3-codex", "gpt-4o"):
        assert call("openai", model, **eu).unpriced_reason == NO_REGIONAL_PRICE, model
    combined = call("openai", "gpt-6.1-sol", **eu, **{"llm.service_tier": "fast"})
    assert combined.unpriced_reason == NO_REGIONAL_PRICE  # no page prices the combination


def test_gemini_text_prices_and_modifiers() -> None:
    assert total("google", "gemini-2.5-flash", tokens_out=M) == D("2.8")
    assert total("google", "gemini-3.1-flash-lite", **{"llm.cached_input_tokens": M}) == D("0.025")
    first = total("google", "gemini-3.6-flash")
    assert first == D("0.75")
    later = ENGINE.calculate(
        llm(
            {
                "llm.provider": "google",
                "llm.model": "gemini-3.6-flash",
                "llm.input_tokens": M,
            },
            day=7,
        ).model_copy(update={"occurred_at": datetime(2027, 1, 1, tzinfo=UTC)})
    )
    assert later.total == D("1.5")
    assert (
        call("google", "gemini-2.5-flash", **{"llm.service_tier": "batch"}).unpriced_reason
        == NO_PRICE_FOR_TIER
    )
    assert (
        call("google", "gemini-2.5-flash", **{"llm.data_residency": "eu"}).unpriced_reason
        == NO_REGIONAL_PRICE
    )


# ------------------------------------------------------------------ determinism and history


def test_the_later_version_wins_a_tie_in_either_order_and_a_pin_reproduces_the_earlier() -> None:
    entries = list(BUILTIN_ENTRIES)
    forward, backward = CostEngine(PriceBook(entries)), CostEngine(PriceBook(entries[::-1]))
    event = llm({"llm.provider": "openai", "llm.model": "gpt-5.4", "llm.input_tokens": M}, day=13)
    assert forward.calculate(event) == backward.calculate(event)
    assert forward.calculate(event).pricing_version == BATCH2_VERSION
    pinned = forward.calculate(event, pricing_version=TIERED_VERSION)
    assert (
        pinned.pricing_version == TIERED_VERSION and pinned.total == forward.calculate(event).total
    )


def test_a_pin_to_the_earlier_version_does_not_know_the_new_modifiers() -> None:
    event = llm(
        {
            "llm.provider": "openai",
            "llm.model": "gpt-5.4",
            "llm.input_tokens": M,
            "llm.service_tier": "batch",
        },
        day=13,
    )
    line = ENGINE.calculate(event, pricing_version=TIERED_VERSION)
    assert (line.source, line.unpriced_reason) == ("unpriced", NO_PRICE_FOR_TIER)


def test_every_earlier_standard_price_is_unchanged_by_the_republication() -> None:
    old_engine = CostEngine(PriceBook(TIERED_ENTRIES))
    for old in TIERED_ENTRIES:
        if old.provider == "google":
            continue
        for prompt in (1_000, (old.min_prompt_tokens or 0) + 1):
            event = llm(
                {
                    "llm.provider": old.provider,
                    "llm.model": old.model_pattern.rstrip("*").replace(
                        "claude-haiku-5-5", "claude-haiku-5-5-20261001"
                    ),
                    "llm.input_tokens": prompt,
                    "llm.cached_input_tokens": min(500, prompt),
                    "llm.output_tokens": 777,
                },
                day=13,
            )
            before, after = old_engine.calculate(event), ENGINE.calculate(event)
            assert before.source == after.source == "estimated", old.model_pattern
            assert (before.input_cost, before.cached_cost, before.output_cost) == (
                after.input_cost,
                after.cached_cost,
                after.output_cost,
            ), (old.model_pattern, prompt)


# ------------------------------------------------------------------ recorded vendor data


def test_every_batch_2_entry_records_its_source_and_fetch_date() -> None:
    assert len(BATCH2_ENTRIES) > 150
    for e in BATCH2_ENTRIES:
        assert e.pricing_version == BATCH2_VERSION and e.origin == "builtin"
        assert e.valid_from in {
            datetime(2026, 10, 9, tzinfo=UTC),  # the Gemini correction
            datetime(2026, 10, 12, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        }
        assert e.source.startswith("https://"), e.model_pattern
        assert re.search(r"read (twice )?2026-10-12", e.source), e.model_pattern
        assert e.service_tier in KNOWN_TIERS and e.currency == "USD"


def test_batch_2_adds_the_models_earlier_batches_left_out() -> None:
    for provider, model in (
        ("openai", "gpt-6-sol"),
        ("openai", "gpt-5.6-terra"),
        ("openai", "gpt-5.6-luna"),
        ("openai", "o4-mini"),
        ("openai", "o3"),
        ("openai", "o3-mini"),
        ("openai", "o3-pro"),
        ("openai", "o1"),
        ("openai", "o1-pro"),
        ("anthropic", "claude-opus-5"),
        ("anthropic", "claude-opus-4-8-20260101"),
        ("anthropic", "claude-fable-5"),
        ("anthropic", "claude-mythos-5-1"),
        ("anthropic", "claude-sonnet-5"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("google", "gemini-3.6-flash"),
        ("google", "gemini-3-flash-preview"),
        ("google", "gemini-2.5-flash-lite"),
    ):
        assert BOOK.find(provider, model, at(13)) is not None, model


def test_models_left_out_stay_unpriced() -> None:
    # gpt-5.5-pro: its model page states no long-context rule (the pricing page shows one).
    for provider, model in (
        ("openai", "gpt-5.5-pro"),
        ("openai", "chat-latest"),
        ("anthropic", "claude-opus-4-1"),
        ("anthropic", "claude-opus-4-20250514"),
        ("anthropic", "claude-sonnet-4-20250514"),
        ("anthropic", "claude-3-5-haiku-20241022"),
        ("anthropic", "claude-opus-5-0-preview"),
    ):
        assert BOOK.find(provider, model, at(13)) is None, model


def test_standard_prices_of_the_new_openai_models() -> None:
    expected = {
        "gpt-6-sol": ("2", "0.20", "10", "4", "0.40", "15"),
        "gpt-5.6-terra": ("2", "0.20", "12", "4", "0.40", "18"),
        "gpt-5.6-luna": ("0.20", "0.02", "1.20", "0.40", "0.04", "1.80"),
    }
    for model, (i, c, o, li, lc, lo) in expected.items():
        short = BOOK.find("openai", model, at(13), prompt_tokens=272_000)
        long_ = BOOK.find("openai", model, at(13), prompt_tokens=272_001)
        assert short and long_ and long_.min_prompt_tokens == 272_000
        assert (
            short.input_per_million,
            short.cached_input_per_million,
            short.output_per_million,
        ) == (D(i), D(c), D(o))
        assert (
            long_.input_per_million,
            long_.cached_input_per_million,
            long_.output_per_million,
        ) == (D(li), D(lc), D(lo))
        assert short.cache_write_per_million == D(i) * D(
            "1.25"
        )  # "billed at 1.25x the uncached input rate"
    o_series = {
        "o4-mini": ("1.10", "0.275", "4.40"),
        "o3": ("2", "0.50", "8"),
        "o3-mini": ("1.10", "0.55", "4.40"),
        "o3-pro": ("20", None, "80"),
        "o1": ("15", "7.50", "60"),
        "o1-pro": ("150", None, "600"),
    }
    for model, (i, c, o) in o_series.items():
        found = BOOK.find("openai", model, at(13), prompt_tokens=900_000)
        assert found and found.min_prompt_tokens is None, model
        assert (found.input_per_million, found.output_per_million) == (D(i), D(o)), model
        assert found.cached_input_per_million == (D(c) if c else None), model


def test_anthropic_batch_and_cache_prices_match_the_vendors_tables() -> None:
    # (pattern, batch input, batch output) exactly as the Batch processing table lists them, and
    # the 5m/1h cache-write columns of the model pricing table.
    batch_table = {
        "claude-fable-5-1*": ("5", "25"),
        "claude-mythos-5-1*": ("5", "25"),
        "claude-fable-5": ("5", "25"),
        "claude-mythos-5": ("5", "25"),
        "claude-opus-5-5*": ("2", "10"),
        "claude-opus-5": ("2.50", "12.50"),
        "claude-opus-4-8*": ("2.50", "12.50"),
        "claude-opus-4-7*": ("2.50", "12.50"),
        "claude-opus-4-6*": ("2.50", "12.50"),
        "claude-opus-4-5*": ("2.50", "12.50"),
        "claude-sonnet-5-5*": ("1", "5"),
        "claude-sonnet-5": ("1", "5"),
        "claude-sonnet-4-6*": ("1.50", "7.50"),
        "claude-sonnet-4-5*": ("1.50", "7.50"),
        "claude-haiku-4-5*": ("0.50", "2.50"),
    }
    writes = {  # 5m, 1h
        "claude-fable-5-1*": ("12.50", "20"),
        "claude-opus-5-5*": ("5", "8"),
        "claude-opus-5": ("6.25", "10"),
        "claude-sonnet-5-5*": ("2.50", "4"),
        "claude-sonnet-4-6*": ("3.75", "6"),
        "claude-haiku-4-5*": ("1.25", "2"),
    }
    rows = {
        (e.model_pattern, e.service_tier, e.min_prompt_tokens): e
        for e in BATCH2_ENTRIES
        if e.provider == "anthropic"
    }
    for pattern, (bin_, bout) in batch_table.items():
        batch = rows[(pattern, "batch", None)]
        assert (batch.input_per_million, batch.output_per_million) == (D(bin_), D(bout)), pattern
    for pattern, (w5, w1h) in writes.items():
        std = rows[(pattern, "standard", None)]
        assert (std.cache_write_per_million, std.cache_write_1h_per_million) == (D(w5), D(w1h))
        assert std.cache_write_per_million == std.input_per_million * D("1.25")
        assert std.cache_write_1h_per_million == std.input_per_million * 2
    haiku_low, haiku_high = (
        rows[("claude-haiku-5-5*", "batch", None)],
        rows[("claude-haiku-5-5*", "batch", 100_000)],
    )
    assert (haiku_low.input_per_million, haiku_low.output_per_million) == (D("0.05"), D("0.25"))
    assert (haiku_high.input_per_million, haiku_high.output_per_million) == (D("0.25"), D("1.25"))
    fast = {
        p: (rows[(p, "fast", None)].input_per_million, rows[(p, "fast", None)].output_per_million)
        for p in ("claude-opus-5-5*", "claude-opus-5", "claude-opus-4-8*")
    }
    assert fast == {
        "claude-opus-5-5*": (D(8), D(40)),
        "claude-opus-5": (D(10), D(50)),
        "claude-opus-4-8*": (D(10), D(50)),
    }
    assert {p for p, tier, _ in rows if tier == "fast"} == {*fast, "claude-opus-5-20*"}
    regional = {
        p
        for (p, tier, _), e in rows.items()
        if tier == "standard" and e.regional_multiplier == D("1.1")
    }
    assert "claude-opus-4-5*" not in regional and "claude-haiku-4-5*" not in regional
    assert "claude-opus-4-6*" in regional and "claude-haiku-5-5*" in regional


def test_openai_batch_flex_and_fast_rows_that_the_pricing_page_lists() -> None:
    rows = {
        (e.model_pattern, e.service_tier, e.min_prompt_tokens): e
        for e in BATCH2_ENTRIES
        if e.provider == "openai"
    }
    tiers = {t for _, t, _ in rows}
    assert tiers == {"standard", "batch", "flex", "fast", "ultrafast"}
    # Page quirks kept as stated: Batch/Flex gpt-5.4 cached price is shown as 0.13 and Flex o4-mini
    # as 0.138 (rounded by the vendor); Batch has no cached price for gpt-4.1.
    assert rows[("gpt-5.4", "batch", None)].cached_input_per_million == D("0.13")
    assert rows[("o4-mini", "flex", None)].cached_input_per_million == D("0.138")
    assert rows[("gpt-4.1", "batch", None)].cached_input_per_million is None
    assert ("gpt-4.1", "flex", None) not in rows and ("o1", "fast", None) not in rows
    assert ("gpt-5.5-pro", "standard", None) not in rows
    for model, tier in (("gpt-6-astra", "ultrafast"), ("gpt-6.1-sol", "ultrafast")):
        assert (model, tier, 272_000) in rows
    assert not [
        k for k in rows if k[1] == "ultrafast" and k[0] not in ("gpt-6-astra", "gpt-6.1-sol")
    ]
    promo = rows[("gpt-5.6-sol", "batch", None)]
    assert "2026-11-21" in promo.source


def test_openai_standard_rows_equal_the_earlier_batch() -> None:
    old = {
        (e.model_pattern, e.min_prompt_tokens): e for e in TIERED_ENTRIES if e.provider == "openai"
    }
    new = {
        (e.model_pattern, e.min_prompt_tokens): e
        for e in BATCH2_ENTRIES
        if e.provider == "openai" and e.service_tier == "standard"
    }
    gone = {(m, None) for m in SHUT_DOWN}  # published earlier, shut down since (not re-entered)
    assert set(old) - gone <= set(new)
    for key, row in old.items():
        if key in gone:
            continue
        assert (
            row.input_per_million,
            row.cached_input_per_million,
            row.output_per_million,
        ) == (
            new[key].input_per_million,
            new[key].cached_input_per_million,
            new[key].output_per_million,
        ), key


def test_the_event_contract_names_the_modifier_attributes() -> None:
    from abb_event_schema.registry import KNOWN_ATTRIBUTES

    from abb_api.content import is_content_attribute

    for key in (
        "llm.service_tier",
        "llm.data_residency",
        "llm.cache_write_tokens",
        "llm.cache_write_1h_tokens",
    ):
        assert key in KNOWN_ATTRIBUTES
        assert not is_content_attribute(key), key  # metadata, visible to every reader (INV-5)


def test_a_viewer_without_payload_read_sees_the_modifier_attributes() -> None:
    from abb_api.runs.content import withhold_attributes

    attributes = {
        "llm.service_tier": "batch",
        "llm.data_residency": "us",
        "llm.cache_write_tokens": 5,
        "llm.cache_write_1h_tokens": 2,
        "shell.command": "cat secrets",
    }
    shown, hidden = withhold_attributes(attributes)
    assert hidden == ["shell.command"]
    assert {k: v for k, v in shown.items() if k.startswith("llm.")} == {
        k: v for k, v in attributes.items() if k.startswith("llm.")
    }


@pytest.mark.parametrize("bad", [True, -5, "40", 1.5, None])
def test_hostile_cache_write_values_are_ignored(bad: Any) -> None:
    event = llm(
        {
            "llm.provider": "openai",
            "llm.model": "gpt-6.1-sol",
            "llm.input_tokens": 100_000,
            "llm.cache_write_tokens": bad,
        },
        day=13,
        raw=True,
    )
    assert ENGINE.calculate(event).total == D("0.2")  # 100k plain input at 2 per million


SHUT_DOWN = ("davinci-002", "babbage-002", "gpt-3.5-turbo-1106", "gpt-3.5-turbo-instruct")
SHUTS_DOWN_1023 = (
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-0125",
    "gpt-4o-2024-05-13",
    "gpt-4-turbo-2024-04-09",
    "gpt-4-0613",
    "o1",
    "o1-pro",
    "o3-mini",
    "o4-mini",
)


def test_models_the_deprecations_page_has_already_shut_down_are_not_entered() -> None:
    # Deprecations page, read twice 2026-10-12: shut down 2026-09-28.
    entered = {e.model_pattern for e in BATCH2_ENTRIES if e.provider == "openai"}
    assert not entered & set(SHUT_DOWN)


def test_models_with_a_stated_shutdown_date_stop_being_priced_on_it() -> None:
    cutoff = datetime(2026, 10, 23, tzinfo=UTC)
    for model in SHUTS_DOWN_1023:
        rows = [e for e in BATCH2_ENTRIES if e.provider == "openai" and e.model_pattern == model]
        assert rows, model
        for row in rows:
            assert row.valid_to == cutoff, (model, row.service_tier)
            assert "2026-10-23" in row.source
        assert BOOK.find("openai", model, datetime(2026, 10, 22, 23, tzinfo=UTC)) is not None
    # o1 has no row in the earlier batch, so nothing prices it from the shutdown on.
    assert BOOK.find("openai", "o1", datetime(2026, 10, 23, tzinfo=UTC)) is None
    # A model that is not on the list keeps no end date.
    assert all(e.valid_to is None for e in BATCH2_ENTRIES if e.model_pattern == "gpt-4.1")


def test_gemini_3_5_flash_lite_cached_tokens_use_the_stated_cache_price() -> None:
    # The pricing page states $0.03 for context caching (read twice 2026-10-12); the earlier
    # version left it unset, which billed cached tokens at the input price.
    cached = {"llm.cached_input_tokens": M}
    assert total("google", "gemini-3.5-flash-lite", **cached) == D("0.03")
    for day in (9, 10, 11):  # the correction covers the whole span of the version it corrects
        event = llm(
            {
                "llm.provider": "google",
                "llm.model": "gemini-3.5-flash-lite",
                "llm.input_tokens": M,
                "llm.cached_input_tokens": M,
            },
            day=day,
        )
        assert ENGINE.calculate(event).total == D("0.03"), day
    pinned = ENGINE.calculate(
        llm(
            {
                "llm.provider": "google",
                "llm.model": "gemini-3.5-flash-lite",
                "llm.input_tokens": M,
                "llm.cached_input_tokens": M,
            },
            day=13,
        ),
        pricing_version="2026-10-09",
    )
    assert pinned.total == D("0.30")  # the published version still reproduces itself
