"""Both drivers must pass every supported conformance scenario (ADR-051)."""

import pytest
from abb_conformance import SCENARIOS, check_scenario

from tests.drivers import FakeDriver, RealDriver, real_available

DRIVERS = [
    pytest.param(FakeDriver(), id="fake"),
    pytest.param(
        RealDriver(),
        id="real",
        marks=pytest.mark.skipif(not real_available(), reason="langgraph is not installed"),
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("driver", DRIVERS)
def test_scenario(driver, scenario):  # type: ignore[no-untyped-def]
    supported = scenario in driver.supports
    assert check_scenario(driver, scenario) is supported
