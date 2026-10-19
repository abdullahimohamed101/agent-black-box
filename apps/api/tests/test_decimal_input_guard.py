"""Decimal-string inputs stay strictly validated.

Pydantic 2.14 stopped emitting a `pattern` for Decimal strings in the OpenAPI schema; this
guards that validation itself (not just the published schema) still rejects garbage.
"""

import pytest
from pydantic import ValidationError

from abb_api.cost.router import CreateOverride


def _body(**price: object) -> dict[str, object]:
    return {"model_pattern": "m*", "input_per_million": "1", "output_per_million": "1", **price}


@pytest.mark.parametrize(
    "bad", ["abc", "--1", "-", "+", ".", "", "1e400", "NaN", "Infinity", "-1", "1.0000000001", "1e7"]
)
def test_garbage_decimal_strings_are_rejected(bad: str) -> None:
    for field in ("input_per_million", "output_per_million", "request_price"):
        with pytest.raises(ValidationError):
            CreateOverride.model_validate(_body(**{field: bad}))


@pytest.mark.parametrize("good", ["0", "1", "2.5", "0.000000001", "1000000", 3, 0.5])
def test_well_formed_decimal_inputs_are_accepted(good: object) -> None:
    CreateOverride.model_validate(_body(input_per_million=good))
