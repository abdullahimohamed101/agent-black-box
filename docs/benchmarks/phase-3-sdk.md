# Phase 3 benchmark: SDK overhead on the application thread

Spec §156 target: under 1 ms typical. Reproduce: `cd packages/sdk-python && uv run python bench.py`.

Environment: Apple M5 Pro, CPython 3.10.22, one run, the exporter thread active and shipping every event to a local stub
server (so GIL contention with batching, JSON, gzip and HTTP is included). 20,000 iterations per row (5,000 for the payload row).
Numbers are what the *caller* pays; nothing in the application thread does I/O.

| Operation | mean | p50 | p99 | max |
| --- | --: | --: | --: | --: |
| `event()` with 3 attributes | 6.9 us | 6.3 us | 13 us | 3.3 ms |
| `span()` enter + exit (2 events) | 16.5 us | 15.2 us | 28 us | 8.8 ms |
| `llm_call()` enter + exit (2 events) | 20.4 us | 19.1 us | 31 us | 11.7 ms |
| `event()` with a string that triggers the secret scan | 12.2 us | 10.2 us | 25 us | 22 ms |
| `event()` with a 4 KB payload (`FULL` mode) | 22.6 us | 21.2 us | 40 us | 0.7 ms |

All 120,002 events of the 100,000-event main loop were delivered (`events_sent` = `events_created`, 1,201 batches, no drops).

Reading: typical and p99 costs are 25-150x under the 1 ms target. The `max` column is one-off stalls (garbage collection and the
exporter thread holding the GIL while it serializes a batch); they are single samples out of 20,000 and none exceeded 25 ms.
Run on a loaded CI machine the absolute numbers will be higher; the benchmark is not a gate, it is a regression reference.
