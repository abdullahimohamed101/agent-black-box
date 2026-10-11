# Phase 9 regression check: `derive_run` on a 30,000-event run (AC-9)

Question: did the Phase 9 change to `runs/summary.py` (per-agent figures, the root agent and `trace_id` from `run.started`,
`SUMMARY_VERSION` 2 -> 3) make the summarizer's CPU cost on a very large run worse (KI-016, KI-022)?

Method: in-process, no database. 30,002 events (one `run.started`, repeating groups of an LLM call with token counts, a tool call and a
`file.read`, one `run.completed`), built once with `tests/ingest_helpers.build_event`. `derive_run(events)` is timed with
`time.perf_counter` after one warm-up call, three measured calls per process; the whole experiment was run twice. Before = the
`apps/api/src` tree of `c212058` (the merge base, extracted with `git archive` and put first on `PYTHONPATH`; the module path was printed
to prove which code ran), after = this branch. Same machine (macOS, Apple silicon), same Python 3.12 venv (apps/api), sequential runs. Two shapes: one
agent, and five agents (the example's names) interleaved at random. The script is reproduced below.

| Shape | Run | Before: three calls, median (ms) | After: three calls, median (ms) |
| --- | --- | --- | --- |
| 1 agent | 1 | 122.2, 119.3, 96.5, **119.3** | 125.1, 135.0, 105.7, **125.1** |
| 1 agent | 2 | 111.9, 115.5, 88.3, **111.9** | 144.9, 131.9, 113.0, **131.9** |
| 5 agents | 1 | 119.2, 120.9, 95.2, **119.2** | 127.1, 123.5, 99.1, **123.5** |
| 5 agents | 2 | 117.3, 121.0, 97.3, **117.3** | 129.1, 131.5, 107.8, **129.1** |

Mean of the four medians: **117.0 ms before, 127.4 ms after (+10 ms, +9%)**; the single-agent shape +13 ms, the five-agent shape +8 ms. The
spread inside one process (the third call is always about 20% faster than the first two, a warm cache effect) is as large as the
difference, so the honest reading is "one extra linear pass, about +10% on the pure derivation, +10 ms on a 30,000-event run".

Reading against KI-016: the 1.7 to 2.6 s stall of a 30,000-event active run is dominated by loading every event from the database and
recomputing after each batch (Phase 2 and Phase 7 notes), not by `derive_run` itself (about 0.12 s here). +10 ms of CPU on a call that already
runs for 1.7 s or more is within the noise of the earlier measurements (p99 swung 1.7 s to 2.4 s between runs), so **no new KI-016
regression**. Not re-run: the HTTP ingest harness of Phase 7 (needs the full stack and minutes of load); this is a micro-measurement of the part
that changed. The per-agent figures also bound their output: `summary.agents` holds at most 50 entries and list responses omit it.

## Script

```python
"""Time derive_run in-process on a 30,000-event run (AC-9). Same events, same machine, before vs after."""
import statistics, sys, time, uuid, random
import abb_api.runs.summary as summary
from abb_api.runs.summary import SUMMARY_VERSION, derive_run
from abb_api.tenancy import TenantContext
from abb_event_schema.ids import IdKind, new_id
from tests.ingest_helpers import Tenant, build_event, make_run_ids

N = 30_000
MULTI = len(sys.argv) > 1 and sys.argv[1] == "multi"
T = Tenant(TenantContext(uuid.uuid4()), "ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ", {"p": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ"}, {})
run = make_run_ids(); rnd = random.Random(7)
agents = ["planner", "researcher", "coder", "tester", "reviewer"] if MULTI else ["coding-agent"]
events, n = [], 0
def add(t, attrs, **kw):
    global n
    n += 1
    events.append(build_event(T, run, n=n, event_type=t, attributes=attrs, **kw))
add("run.started", {"run.name": "bench"}, agent_id=agents[0])
while n < N - 2:
    a = rnd.choice(agents); sp = new_id(IdKind.SPAN)
    add("llm.request.started", {"llm.provider": "openai", "llm.model": "gpt-4.1"}, span_id=sp, agent_id=a)
    add("llm.request.completed", {"llm.provider": "openai", "llm.model": "gpt-4.1", "llm.input_tokens": 1000, "llm.output_tokens": 200}, span_id=sp, agent_id=a, status="success", duration_ms=900.0)
    sp = new_id(IdKind.SPAN)
    add("tool.call.started", {"tool.name": "search"}, span_id=sp, agent_id=a)
    add("tool.call.completed", {"tool.name": "search"}, span_id=sp, agent_id=a, status="success", duration_ms=120.0)
    add("file.read", {"file.path": "a.py"}, agent_id=a)
add("run.completed", {}, status="success")
print(f"events={len(events)} agents={len(agents)} SUMMARY_VERSION={SUMMARY_VERSION} module={summary.__file__}")
derive_run(events)  # warm-up
times = []
for _ in range(3):
    t = time.perf_counter(); d = derive_run(events); times.append((time.perf_counter() - t) * 1000)
print("ms per derive_run:", [round(x, 1) for x in times], "median", round(statistics.median(times), 1))
print("keys:", sorted(d.summary)[:30])
```

Run from `apps/api`: `PYTHONPATH=<old>/apps/api/src:. uv run python bench_derive.py [multi]` for before, `PYTHONPATH=. uv run python bench_derive.py [multi]` for after.
