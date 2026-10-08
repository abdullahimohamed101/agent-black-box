"""Agent Black Box adapter for LangGraph / LangChain (spec 18, 70; ADR-050..052)."""

from blackbox_langgraph.handler import BlackBoxCallbackHandler

BlackBoxCallback = BlackBoxCallbackHandler

__all__ = ["BlackBoxCallback", "BlackBoxCallbackHandler"]
