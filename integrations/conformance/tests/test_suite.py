"""The suite must itself catch broken adapters: a reference driver passes, mutants fail."""

import pytest
from blackbox import BlackBox

from abb_conformance import SCENARIOS, ScenarioError, check_scenario
from abb_conformance.scenarios import (
    CONCURRENCY,
    LLM_CACHED,
    LLM_INPUT,
    LLM_OUTPUT,
    PASSWORD_ASSIGNMENT,
    SECRET,
    TOOL_NAME,
)


class Reference:
    """Calls the SDK directly: defines what a correct adapter must produce."""

    name = "reference"
    supports = frozenset(SCENARIOS)
    llm_provider, llm_model = "acme", "m1"
    skip_usage = False
    swallow = False
    emit_late = False

    def perform(self, scenario: str, bb: BlackBox) -> None:
        with bb.run("conformance") as run:
            if scenario == "sensitive_full":
                with run.span(TOOL_NAME, kind="tool") as span:
                    span.set_payload({"in": SECRET, "out": PASSWORD_ASSIGNMENT})
            elif scenario.startswith("tool") or scenario == "sensitive":
                self._tool(run, TOOL_NAME, fail=scenario == "tool_failure")
            elif scenario.startswith("llm"):
                self._llm(run, fail=scenario == "llm_failure")
            elif scenario == "nested":
                with run.span("step", kind="agent"):
                    self._tool(run, TOOL_NAME)
                    self._llm(run)
            elif scenario == "concurrent":
                for i in range(CONCURRENCY):
                    self._tool(run, f"{TOOL_NAME}-{i}")
            elif scenario == "late_end":
                span = run.span(TOOL_NAME, kind="tool").start()
                run.end()
                if not self.emit_late:
                    return
                span.end()  # a correct adapter checks `run.ended` first
            elif scenario in ("hostile", "redactor_raises"):
                self._tool(run, TOOL_NAME)

    def _tool(self, run, name, fail=False):  # type: ignore[no-untyped-def]
        try:
            with run.span(name, kind="tool"):
                if fail:
                    raise ScenarioError("boom")
        except ScenarioError:
            if self.swallow:
                return
            raise

    def _llm(self, run, fail=False):  # type: ignore[no-untyped-def]
        with run.llm_call(self.llm_provider, self.llm_model) as llm:
            if fail:
                raise ScenarioError("boom")
            if not self.skip_usage:
                llm.record_usage(LLM_INPUT, LLM_OUTPUT, cached_input_tokens=LLM_CACHED)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_reference_passes(scenario: str) -> None:
    assert check_scenario(Reference(), scenario) is True


def test_unsupported_scenarios_are_reported_not_passed() -> None:
    driver = Reference()
    driver.supports = frozenset({"tool"})
    assert check_scenario(driver, "llm") is False


def test_missing_usage_is_caught() -> None:
    driver = Reference()
    driver.skip_usage = True
    with pytest.raises(KeyError):
        check_scenario(driver, "llm")


def test_an_event_after_the_run_end_is_caught() -> None:
    driver = Reference()
    driver.emit_late = True
    with pytest.raises(AssertionError, match="run end"):
        check_scenario(driver, "late_end")


def test_swallowed_framework_error_is_caught() -> None:
    driver = Reference()
    driver.swallow = True
    with pytest.raises(AssertionError, match="swallowed"):
        check_scenario(driver, "tool_failure")


def test_wrong_tool_name_is_caught() -> None:
    class Renamed(Reference):
        def _tool(self, run, name, fail=False):  # type: ignore[no-untyped-def]
            super()._tool(run, "other", fail)

    with pytest.raises(AssertionError):
        check_scenario(Renamed(), "tool")


def test_golden_normalization_is_deterministic_and_valid(tmp_path):  # type: ignore[no-untyped-def]
    from abb_conformance import assert_golden, capture, normalize, validate_events

    def one(bb: BlackBox) -> None:
        Reference().perform("nested", bb)

    first, second = normalize(capture(one)), normalize(capture(one))
    assert first == second
    validate_events(first)
    path = tmp_path / "g.json"
    import json

    path.write_text(json.dumps(first))
    assert_golden(path, capture(one))
    first[2]["attributes"]["tool.name"] = "drifted"
    path.write_text(json.dumps(first))
    with pytest.raises(AssertionError, match="golden"):
        assert_golden(path, capture(one))
