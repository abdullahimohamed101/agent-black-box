"""Versioned price entries and the lookup that selects one (spec §79.1, ADR-040).

Entries are immutable data. Selection depends only on the entry set, the model, the provider and
the event time, never on the wall clock, so recomputing history later gives the same answer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatchcase

ZERO = Decimal(0)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# The service tiers a price can be stated for (ADR-044). A call that names any other tier is left
# unpriced: its price was never read from a vendor page, so standard would be a guess.
STANDARD = "standard"
KNOWN_TIERS: tuple[str, ...] = (STANDARD, "batch", "flex", "fast", "ultrafast")


@dataclass(frozen=True)
class PriceEntry:
    pricing_version: str
    model_pattern: str  # glob, case-insensitive (`*` and `?`)
    valid_from: datetime
    input_per_million: Decimal
    output_per_million: Decimal
    provider: str | None = None  # None: any provider
    valid_to: datetime | None = None  # exclusive
    cached_input_per_million: Decimal | None = None  # None: cached tokens cost the input price
    request_price: Decimal = ZERO
    currency: str = "USD"
    source: str = ""
    origin: str = "builtin"  # "builtin" | "override"
    project_id: str | None = None  # overrides only: None = whole workspace
    created_at: datetime | None = None  # overrides only: the newer of two equal rows wins
    # A prompt-size tier: applies only to calls whose prompt tokens (cached ones included) are
    # strictly greater than this, and then prices the whole request at this entry's rates (the
    # vendors' "over 272K" rule), not just the tokens above the line. None: any prompt size.
    min_prompt_tokens: int | None = None
    # The upper bound of a prompt-size tier whose larger tier the vendor does not price ("-" in its
    # table): above it nothing applies and the call is unpriced rather than charged the small rates.
    max_prompt_tokens: int | None = None
    # The service tier this entry prices (ADR-044). None: every tier (workspace overrides, which
    # state one price the workspace pays).
    service_tier: str | None = STANDARD
    # Cache-write prices per million tokens; None: written tokens cost the input price. The 1-hour
    # price falls back to the 5-minute one.
    cache_write_per_million: Decimal | None = None
    cache_write_1h_per_million: Decimal | None = None
    # Factor for calls that ran on a regional endpoint (`llm.data_residency` other than "global"),
    # only where the vendor's page states it for this model and tier; None: no stated price, so
    # such a call is unpriced. Overrides are never scaled.
    regional_multiplier: Decimal | None = None

    def applies(
        self,
        provider: str | None,
        model: str,
        at: datetime,
        prompt_tokens: int = 0,
        service_tier: str | None = STANDARD,
    ) -> bool:
        if self.valid_from > at or (self.valid_to is not None and at >= self.valid_to):
            return False
        if self.min_prompt_tokens is not None and prompt_tokens <= self.min_prompt_tokens:
            return False
        if self.max_prompt_tokens is not None and prompt_tokens > self.max_prompt_tokens:
            return False
        if (
            service_tier is not None
            and self.service_tier is not None
            and self.service_tier != service_tier
        ):
            return False
        if self.provider is not None and (provider or "").lower() != self.provider.lower():
            return False
        return fnmatchcase(model.lower(), self.model_pattern.lower())

    def specificity(self) -> tuple[int, int, int, int, int, int, datetime, datetime, str]:
        """Bigger wins: override, project scope, provider-specific, longer literal, a row for this
        very tier over an any-tier one, higher satisfied prompt tier (before the start date, so a
        tier beats its base row whatever their dates), newer start, newer row (so a correction with
        the same pattern and start replaces the earlier one), later pricing version (so a batch
        that re-publishes a price with the same start supersedes the earlier batch)."""
        literal = sum(1 for ch in self.model_pattern if ch not in "*?")
        return (
            1 if self.origin == "override" else 0,
            1 if self.project_id is not None else 0,
            1 if self.provider is not None else 0,
            literal,
            1 if self.service_tier is not None else 0,
            self.min_prompt_tokens if self.min_prompt_tokens is not None else -1,
            self.valid_from,
            self.created_at or _EPOCH,
            self.pricing_version,
        )


class PriceBook:
    """A set of entries. `find` returns the single entry that prices a call, or None."""

    def __init__(self, entries: tuple[PriceEntry, ...] | list[PriceEntry]) -> None:
        self._entries = tuple(entries)

    @property
    def entries(self) -> tuple[PriceEntry, ...]:
        return self._entries

    def find(
        self,
        provider: str | None,
        model: str,
        at: datetime,
        *,
        pricing_version: str | None = None,
        prompt_tokens: int = 0,
        service_tier: str | None = STANDARD,
    ) -> PriceEntry | None:
        """`pricing_version` pins built-in entries to one version (reproducing an old calculation).

        Overrides carry their own `override:<id>` version, so a pin selects them the same way.
        `prompt_tokens` selects a prompt-size tier among otherwise equal entries; `service_tier`
        the tier the call ran on (None: any, to ask whether the model is priced at all)."""
        candidates = [
            e
            for e in self._entries
            if e.applies(provider, model, at, prompt_tokens, service_tier)
            and (pricing_version is None or e.pricing_version == pricing_version)
        ]
        return max(candidates, key=PriceEntry.specificity, default=None)
