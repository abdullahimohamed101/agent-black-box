"""The host-agent half of gate 2 of scripts/hardening-e2e.sh: the REAL Python SDK with a key whose id the API is throttling.

    uv run --project packages/sdk-python python scripts/hardening_e2e_sdk.py

Environment: E2E_SDK_ENDPOINT, E2E_SDK_KEY (a well-formed key with a wrong secret; not a real credential), E2E_SDK_PROJECT.
It records far more events than the SDK queue holds, then shuts down with a deadline. The host must finish, in bounded time, with a
bounded queue; every event is either dropped and counted or never sent. Prints one JSON line. Standard library plus `agent_blackbox` only.
"""

import json
import os
import time

from agent_blackbox import BlackBox

MAX_QUEUE = 200
started = time.monotonic()
bb = BlackBox(
    api_key=os.environ["E2E_SDK_KEY"],
    endpoint=os.environ["E2E_SDK_ENDPOINT"],
    project=os.environ.get("E2E_SDK_PROJECT", "hardening"),
    agent_id="throttled-host",
    flush_interval=0.1,
    max_queue=MAX_QUEUE,
)
finished = False
try:
    with bb.run("throttled host run") as run:
        for i in range(400):
            span = run.span(f"step-{i}", kind="tool")
            span.start()
            span.end("success")
    bb.shutdown(5)
    finished = True  # the host code after the SDK calls ran: nothing the SDK did crashed it
finally:
    stats = bb.stats()
    dropped = sum(v for k, v in stats.items() if k.startswith("dropped_"))
    print(json.dumps({
        "host_finished": finished, "elapsed_seconds": round(time.monotonic() - started, 2), "queue_size": stats["queue_size"],
        "max_queue": MAX_QUEUE, "events_created": stats["events_created"], "events_sent": stats["events_sent"], "dropped_total": dropped,
        "retries": stats["retries"], "batches_sent": stats["batches_sent"], "internal_errors": stats["internal_errors"],
    }))
