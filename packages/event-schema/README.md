# abb-event-schema

The Agent Black Box canonical telemetry contract, schema version `1.0`. Everything that
produces or consumes agent telemetry (SDKs, adapters, the API, the web app) depends on this
package and nothing here depends on them. Its only runtime dependency is `pydantic`.

```python
from datetime import datetime, timezone

from abb_event_schema.event import finalize
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.ordering import sort_events
from abb_event_schema.parse import dumps, parse_event_in

run_id, trace_id = new_id(IdKind.RUN), new_id(IdKind.TRACE)
wire = {
    "schema_version": "1.0",
    "event_id": new_id(IdKind.EVENT),
    "run_id": run_id,
    "trace_id": trace_id,
    "span_id": new_id(IdKind.SPAN),
    "agent_id": "coding-agent",
    "event_type": "tool.call.completed",
    "occurred_at": datetime.now(timezone.utc).isoformat(),
    "sequence": 1,
    "status": "success",
    "duration_ms": 843,
    "attributes": {"tool.name": "github", "tool.operation": "search_issues"},
}

event = parse_event_in(wire)  # validate what a client sent
stored = finalize(  # bind it to the tenant from the API key
    event,
    workspace_id=new_id(IdKind.WORKSPACE),
    project_id=new_id(IdKind.PROJECT),
    received_at=datetime.now(timezone.utc),
)
assert [e.event_id for e in sort_events([stored])] == [event.event_id]
print(dumps(stored).decode())
```

Reference: `docs/architecture/events.md`. Machine-readable schemas: `schemas/1.0/`
(regenerate with `make schema`; CI fails if they are stale). Fixtures for every registered
event type: `examples/valid/`; rejected cases with their expected error codes:
`examples/invalid/`.

## Rules worth remembering

- Events are immutable; identity is `(workspace_id, event_id)`; the first write wins.
- Never trust arrival order: use `sort_events`.
- Unknown attributes are preserved; unknown well-formed event types are accepted.
- Validation errors never echo submitted values.
