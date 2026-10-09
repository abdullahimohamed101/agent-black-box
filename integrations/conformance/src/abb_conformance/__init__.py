"""Shared conformance suite for framework adapters (spec section 70, ADR-051).

An adapter proves it by supplying a `Driver` (how to run each scenario through its framework) and
calling `check_scenario` for every scenario it supports. The suite owns the expectations, so every
adapter is held to the same canonical events for equivalent operations.
"""

from abb_conformance.golden import assert_golden, normalize
from abb_conformance.harness import capture, validate_events
from abb_conformance.scenarios import (
    LLM_CACHED,
    LLM_INPUT,
    LLM_OUTPUT,
    SCENARIOS,
    SECRET,
    TOOL_NAME,
    Driver,
    ScenarioError,
    check_scenario,
)

__all__ = [
    "LLM_CACHED",
    "LLM_INPUT",
    "LLM_OUTPUT",
    "SCENARIOS",
    "SECRET",
    "TOOL_NAME",
    "Driver",
    "ScenarioError",
    "assert_golden",
    "capture",
    "check_scenario",
    "normalize",
    "validate_events",
]
