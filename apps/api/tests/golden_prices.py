"""A line-per-row snapshot of the built-in batch-2 prices, so one edited figure fails a test.

`python -m tests.golden_prices` rewrites the fixture; review the diff against the vendor pages
before committing it (the rows are published prices, ADR-044)."""

from pathlib import Path

from abb_api.cost.builtin import BATCH2_ENTRIES

FIXTURE = Path(__file__).parent / "golden" / "batch2_entries.txt"


def render() -> str:
    def cell(value: object) -> str:
        return "-" if value is None else str(value)

    rows = [
        "|".join(
            cell(v)
            for v in (
                e.provider,
                e.model_pattern,
                e.service_tier,
                e.min_prompt_tokens,
                e.max_prompt_tokens,
                e.valid_from.date(),
                e.valid_to.date() if e.valid_to else None,
                e.input_per_million,
                e.cached_input_per_million,
                e.cache_write_per_million,
                e.cache_write_1h_per_million,
                e.output_per_million,
                e.regional_multiplier,
                ",".join(e.regional_regions) if e.regional_regions else None,
            )
        )
        for e in BATCH2_ENTRIES
    ]
    header = (
        "provider|pattern|tier|min_prompt|max_prompt|valid_from|valid_to|input|cached|write|"
        "write_1h|output|regional|regions"
    )
    return "\n".join([header, *sorted(rows)]) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(render())
