"""Golden cases for `sort_events`, shared with the web app's TypeScript comparator.

The web UI merges live events into a timeline client-side and must order them exactly as the
server does (spec §65.1). `tests/data/ordering-golden.json` is generated here from the Python
implementation; `apps/web/tests/ordering.test.ts` replays it.
Regenerate with `ABB_REGEN_GOLDEN=1 pytest`.
"""

import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from abb_event_schema.ordering import sort_events

GOLDEN = Path(__file__).parent / "data" / "ordering-golden.json"
BASE = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Item:
    event_id: str
    sequence: int | None
    occurred_at: datetime
    received_at: datetime | None


def wire(moment: datetime) -> str:
    """The API's timestamp spelling: UTC, `Z`, fractional digits only when non-zero."""
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{moment.microsecond:06d}Z" if moment.microsecond else f"{stamp}Z"


def make_cases() -> list[dict[str, object]]:
    rng = random.Random(20261007)
    cases: list[dict[str, object]] = []
    for n in range(60):
        size = rng.randint(1, 14)
        style = n % 6
        items = []
        for i in range(size):
            # few distinct instants, so ties on every key are common
            moment = BASE + timedelta(
                seconds=rng.randint(0, 3), microseconds=rng.choice([0, 0, 1, 500, 999999])
            )
            sequence = rng.randint(0, 5)
            if style == 1 and i == 0:
                sequence_value = None  # one event without a sequence: time mode
            elif style == 2:
                sequence_value = None  # none have one
            else:
                sequence_value = sequence
            received = (
                None
                if style == 3 and rng.random() < 0.4
                else BASE + timedelta(milliseconds=rng.randint(0, 4))
            )
            items.append(
                Item(f"evt_{n:03d}{i:03d}{rng.randint(0, 9)}", sequence_value, moment, received)
            )
        shuffled = items[:]
        rng.shuffle(shuffled)
        ordered = sort_events(shuffled)
        mode = "sequence" if all(e.sequence is not None for e in shuffled) else "time"
        cases.append(
            {
                "mode": mode,
                "events": [
                    {
                        "event_id": e.event_id,
                        "sequence": e.sequence,
                        "occurred_at": wire(e.occurred_at),
                        "received_at": None if e.received_at is None else wire(e.received_at),
                    }
                    for e in shuffled
                ],
                "expected": [e.event_id for e in ordered],
            }
        )
    return cases


def test_the_golden_file_matches_the_python_implementation() -> None:
    rendered = json.dumps({"cases": make_cases()}, indent=1) + "\n"
    if os.environ.get("ABB_REGEN_GOLDEN") == "1":
        GOLDEN.write_text(rendered)
    assert GOLDEN.read_text() == rendered, "run `ABB_REGEN_GOLDEN=1 pytest` to regenerate"


def test_the_golden_cases_cover_both_modes_ties_and_missing_values() -> None:
    cases = json.loads(GOLDEN.read_text())["cases"]
    assert {c["mode"] for c in cases} == {"sequence", "time"}
    events = [e for c in cases for e in c["events"]]
    assert any(e["sequence"] is None for e in events) and any(
        e["received_at"] is None for e in events
    )
    assert any(".000001Z" in e["occurred_at"] or ".000500Z" in e["occurred_at"] for e in events)
