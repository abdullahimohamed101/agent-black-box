import pytest
from abb_conformance import SCENARIOS, check_scenario

from tests.drivers import Driver, real_available

DRIVERS = [
    pytest.param(Driver(False), id="fake"),
    pytest.param(
        Driver(True),
        id="real",
        marks=pytest.mark.skipif(not real_available(), reason="mcp is not installed"),
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("driver", DRIVERS)
def test_scenario(driver, scenario):  # type: ignore[no-untyped-def]
    assert check_scenario(driver, scenario) is (scenario in driver.supports)
