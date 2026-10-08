"""Agent Black Box adapter for the OpenAI Python client (spec 18, 70; ADR-050..052)."""

from blackbox_openai.wrap import instrument

__all__ = ["instrument"]
