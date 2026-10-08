"""Agent Black Box adapter for the Anthropic Python client (spec 18, 70; ADR-050..052)."""

from blackbox_anthropic.wrap import instrument

__all__ = ["instrument"]
