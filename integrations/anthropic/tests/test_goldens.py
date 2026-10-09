"""Golden canonical events: drift shows up as a reviewed diff (ADR-051)."""

import json
from pathlib import Path

import pytest
from abb_conformance import ScenarioError, assert_golden, capture, validate_events
from blackbox import BlackBox

from tests.drivers import Driver, real_available

GOLDEN = Path(__file__).parent / "golden"
SCENARIOS = ["llm", "llm_failure", "nested", "sensitive"]
REAL_SCENARIOS = ["llm", "nested", "sensitive"]


def _events(driver, scenario: str):  # type: ignore[no-untyped-def]
    def drive(bb: BlackBox) -> None:
        try:
            driver.perform(scenario, bb)
        except ScenarioError:
            pass  # failure scenarios end by raising the host error

    return capture(drive)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_fake_driver_matches_golden(scenario: str) -> None:
    events = _events(Driver(False), scenario)
    assert_golden(GOLDEN / f"fake_{scenario}.json", events)


@pytest.mark.skipif(not real_available(), reason="openai is not installed")
@pytest.mark.parametrize("scenario", REAL_SCENARIOS)
def test_real_anthropic_matches_golden(scenario: str) -> None:
    events = _events(Driver(True), scenario)
    assert_golden(GOLDEN / f"real_{scenario}.json", events)


def test_every_golden_validates_against_the_event_schema() -> None:
    files = sorted(GOLDEN.glob("*.json"))
    assert len(files) >= len(SCENARIOS)
    for path in files:
        events = json.loads(path.read_text())
        validate_events(events)
        assert all("payload" not in e for e in events), path.name
