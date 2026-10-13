"""The benchmark gate scales its bounds by how slow the machine is, never down (review P3-9)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "security_bench.py"


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("security_bench", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["security_bench"] = module
    spec.loader.exec_module(module)
    return module


def test_a_machine_at_or_below_the_reference_floor_keeps_the_written_bounds(
    bench: ModuleType,
) -> None:
    for floor in (0.5, bench.REFERENCE_FLOOR_MS):
        assert bench.effective_bounds(floor) == bench.BOUNDS


def test_a_slower_machine_scales_every_bound_in_proportion(bench: ModuleType) -> None:
    scaled = bench.effective_bounds(bench.REFERENCE_FLOOR_MS * 4)
    assert scaled == {k: round(v * 4, 1) for k, v in bench.BOUNDS.items()}


def test_the_written_bounds_are_wide_enough_for_a_noisy_runner(bench: ModuleType) -> None:
    assert bench.BOUNDS["batch_p95_ms"] >= 1000.0
