"""Validates site/fixtures/migrate-billing-schema.json against the event contract (packages/event-schema) and recomputes the
figures the hero shows from the events themselves, so the page cannot drift from the contract or from its own arithmetic.

  uv run --project packages/event-schema python -I site/tests/validate_fixture.py [FIXTURE.json]
"""

import json
import sys
from pathlib import Path

from abb_event_schema.parse import parse_event_in

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "fixtures/migrate-billing-schema.json"
doc = json.loads(path.read_text())
events = [parse_event_in(e) for e in doc["events"]]  # raises EventValidationError on any contract violation
problems: list[str] = []

if [e.sequence for e in events] != list(range(1, len(events) + 1)):
    problems.append("sequences must be 1..N in order")
if any(b.occurred_at < a.occurred_at for a, b in zip(events, events[1:])):
    problems.append("occurred_at must not go backwards")
if len(events) != 15 or len(doc["shown"]) != 9:
    problems.append("the page promises '9 of 15 events shown'")

first_error = next((e for e in events if e.status == "error"), None)
if first_error is None or first_error.sequence != 7:
    problems.append("the first error must be step 7 (the headline says 'Failed at step 7')")
else:
    after = (first_error.occurred_at - events[0].occurred_at).total_seconds()
    if round(after, 1) != 6.2:
        problems.append(f"first error at {after}s, the headline says 6.2 s")

tin = sum(e.attributes.get("llm.input_tokens", 0) for e in events)
tout = sum(e.attributes.get("llm.output_tokens", 0) for e in events)
cost = sum(e.attributes.get("cost.estimated_usd", 0.0) for e in events)
retries = sum(1 for e in events if e.event_type == "retry.attempted")
errors = sum(1 for e in events if e.status == "error")
spent_after = sum(e.attributes.get("cost.estimated_usd", 0.0) for e in events if e.sequence > (first_error.sequence if first_error else 0))
f = doc["figures"]
want = {"tokens": f"{tin:,} in / {tout:,} out", "cost": f"${cost:.4f}", "retries": retries, "errors": errors}
for k, v in want.items():
    if f[k] != v:
        problems.append(f"figures.{k} is {f[k]!r}, the events add up to {v!r}")
if f"${spent_after:.4f}" != "$0.0167":
    problems.append(f"spend after the first error is ${spent_after:.4f}, the page says $0.0167")
if round((events[-1].occurred_at - events[0].occurred_at).total_seconds()) != 14:
    problems.append("duration is not 14 s")

for p in problems:
    print(f"FAIL: {p}")
if problems:
    sys.exit(1)
print(f"fixture ok: {len(events)} events validate against event-in, figures and first error recomputed")
