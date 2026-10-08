"""CostEngine: one `CostLine` per completed model call (spec §79.2, ADR-040). Pure."""

import math
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from abb_event_schema.event import Event

from abb_api.cost.pricing import ZERO, PriceBook, PriceEntry

CostSource = Literal["provider_reported", "estimated", "client_estimate", "unpriced"]
COST_SOURCES: tuple[CostSource, ...] = (
    "provider_reported",
    "estimated",
    "client_estimate",
    "unpriced",
)
_QUANT = Decimal("0.000000001")
_MILLION = Decimal(1_000_000)
MAX_TOKENS = 10**12  # absurd counts are hostile input; they are clamped, not trusted
# No single model call costs a million dollars. Larger figures (a hostile attribute, or a price override
# times a clamped token count) are clamped to this, flagged on the line and counted in the run summary, so
# one event can never overflow a money column or dominate a day's totals (never an exception).
MAX_LINE_USD = Decimal("1000000")


@dataclass(frozen=True)
class CostLine:
    event_id: str
    span_id: str | None
    agent_id: str
    occurred_at: datetime
    provider: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    source: CostSource
    total: Decimal  # the effective cost (what totals add up)
    pricing_version: str | None = None
    pricing_origin: str | None = None  # "builtin" | "override" | "event"
    input_cost: Decimal | None = None
    output_cost: Decimal | None = None
    cached_cost: Decimal | None = None
    request_cost: Decimal | None = None
    estimated_total: Decimal | None = None  # what the price book says (when a price applied)
    reported_total: Decimal | None = None  # what the provider reported (when it did)
    client_total: Decimal | None = None  # the caller's own estimate (`cost.estimated_usd`)
    is_retry: bool = False
    clamped: bool = False  # a figure above MAX_LINE_USD was clamped

    def with_retry(self, is_retry: bool) -> "CostLine":
        return replace(self, is_retry=is_retry)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_EVEN)


def _tokens(attrs: dict[str, Any], key: str) -> int | None:
    value = attrs.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, MAX_TOKENS)


def _usd(attrs: dict[str, Any], key: str) -> tuple[Decimal | None, bool]:
    """A cost attribute as money, and whether it was clamped; None when absent or unusable."""
    value = attrs.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, False
    if isinstance(value, float) and not math.isfinite(value):
        return None, False
    if value < 0:
        return None, False
    amount = Decimal(str(value))
    if amount > MAX_LINE_USD:
        return MAX_LINE_USD, True
    return _money(amount), False


def _clamp(amount: Decimal) -> tuple[Decimal, bool]:
    return (MAX_LINE_USD, True) if amount > MAX_LINE_USD else (amount, False)


class CostEngine:
    def __init__(self, prices: PriceBook) -> None:
        self._prices = prices

    def calculate(self, event: Event, *, pricing_version: str | None = None) -> CostLine:
        """Cost of one `llm.request.completed` event.

        Source precedence: provider-reported > estimated from the price book > the caller's
        estimate > unpriced. `input_tokens` includes cached tokens: the uncached remainder is billed
        at the input price, cached ones at the cached price (or the input price when none is set).
        """
        attrs = event.attributes
        provider = attrs.get("llm.provider") if isinstance(attrs.get("llm.provider"), str) else None
        model = attrs.get("llm.model") if isinstance(attrs.get("llm.model"), str) else None
        input_tokens = _tokens(attrs, "llm.input_tokens")
        output_tokens = _tokens(attrs, "llm.output_tokens")
        cached = _tokens(attrs, "llm.cached_input_tokens") or 0
        reported, reported_clamped = _usd(attrs, "cost.provider_usd")
        client, client_clamped = _usd(attrs, "cost.estimated_usd")

        entry: PriceEntry | None = None
        if model is not None and (input_tokens is not None or output_tokens is not None):
            entry = self._prices.find(
                provider, model, event.occurred_at, pricing_version=pricing_version
            )

        line = CostLine(
            event_id=event.event_id,
            span_id=event.span_id,
            agent_id=event.agent_id,
            occurred_at=event.occurred_at,
            provider=provider,
            model=model,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cached_input_tokens=cached,
            source="unpriced",
            total=ZERO,
            reported_total=reported,
            client_total=client,
        )
        if entry is not None:
            billed_cached = min(cached, input_tokens or 0)
            plain = (input_tokens or 0) - billed_cached
            cached_price = entry.cached_input_per_million
            input_cost, c1 = _clamp(_money(plain * entry.input_per_million / _MILLION))
            cached_cost, c2 = _clamp(
                _money(
                    billed_cached
                    * (cached_price if cached_price is not None else entry.input_per_million)
                    / _MILLION
                )
            )
            output_cost, c3 = _clamp(
                _money((output_tokens or 0) * entry.output_per_million / _MILLION)
            )
            request_cost, c4 = _clamp(_money(entry.request_price))
            estimated, c5 = _clamp(input_cost + cached_cost + output_cost + request_cost)
            estimate_clamped = c1 or c2 or c3 or c4 or c5
            line = replace(
                line,
                pricing_version=entry.pricing_version,
                pricing_origin=entry.origin,
                input_cost=input_cost,
                output_cost=output_cost,
                cached_cost=cached_cost,
                request_cost=request_cost,
                estimated_total=estimated,
                clamped=estimate_clamped,
            )
        if reported is not None:
            return replace(
                line,
                source="provider_reported",
                total=reported,
                clamped=line.clamped or reported_clamped,
                pricing_origin=line.pricing_origin or "event",
            )
        if line.estimated_total is not None:
            return replace(line, source="estimated", total=line.estimated_total)
        if client is not None:
            version = attrs.get("cost.pricing_version")
            return replace(
                line,
                source="client_estimate",
                total=client,
                clamped=line.clamped or client_clamped,
                pricing_version=version if isinstance(version, str) else None,
                pricing_origin="event",
            )
        return line


def cost_summary(lines: list[CostLine]) -> dict[str, Any]:
    """Run-level cost figures for the run summary (floats rounded to 9 places, exact sums first)."""

    def usd(value: Decimal) -> float:
        return float(_money(value))

    total = sum((ln.total for ln in lines), ZERO)
    retry = sum((ln.total for ln in lines if ln.is_retry), ZERO)
    by_source = {s: sum((ln.total for ln in lines if ln.source == s), ZERO) for s in COST_SOURCES}
    return {
        "estimated_cost_usd": usd(total),  # the effective total (name kept for compatibility)
        "retry_cost_usd": usd(retry),
        "initial_cost_usd": usd(total - retry),
        "cost_by_source_usd": {
            s: usd(v) for s, v in by_source.items() if v or any(ln.source == s for ln in lines)
        },
        "unpriced_calls": sum(1 for ln in lines if ln.source == "unpriced"),
        "clamped_calls": sum(1 for ln in lines if ln.clamped),
    }
