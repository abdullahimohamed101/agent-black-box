# Independent review of the Phase 10 plan (reliability intelligence)

Reviewed: `docs/plans/active/phase-10-reliability-intelligence.md` at commit 76c366b, branch
`feature/phase-10-reliability-intelligence`, 2026-10-10. Review of the plan on paper, before any code;
no production code, test or migration was changed. Cross-checked against the Phase 9, 11 and 16 plans in
their sibling worktrees (read only).

Method: every load-bearing claim in "Current architecture" was checked against the code in this
worktree (file:line below). Nothing that needs a database or installed dependencies was run (none are
installed here); such claims are marked UNVERIFIED (env). Severity: **P0** critical, **P1** must fix
before implementation, **P2** should fix, **P3** optional.

Summary: no P0. Six P1 (a test the plan forgets to update, a client-triggerable dead letter through the
evidence size CHECK, a detector that flags ordinary runs, exception text reaching the outbox and logs,
health that reports `ok` with no analysis, and an INV-2 equality criterion that is not well defined).
Twelve P2, sixteen P3. The architecture (separate coalesced job, derived rewritten tables, allowlisted
evidence, no per-actor projection) is sound and consistent with ADR-012/061 and INV-1/2/3/7.

## 1. Verified correct (claims checked against code)

- Summarizer flow as described: `jobs/handlers.py:35-67` (`summarize_run` locks, loads, derives,
  applies, replaces cost lines, enqueues `refresh_analytics_day` in the same transaction);
  `RunRepository.lock` is `FOR NO KEY UPDATE` (`runs/repository.py:128-145`, `key_share=True`);
  `PgEventStore.load_run` has no cap and no order (`ingestion/store.py:202-210`); `SUMMARY_VERSION = 2`
  (`runs/summary.py:24`); summary keys match the list in the plan (`runs/summary.py:152-172`); typed
  copies on `runs` (`db/tables.py:303-315`).
- Outbox: coalescing on `(job_type, dedupe_key) WHERE status = 'pending'` (`jobs/outbox.py:94-102`);
  `summarize_key` format `f"{workspace_id}:{run_id}"` (`outbox.py:59-61`); a `running` job does not
  coalesce, so a summarizer that commits while a detect job runs creates a fresh pending job (no lost
  update, the plan's eventual-consistency argument holds); `fail()` marks a retry "superseded" when a
  newer pending job exists (`outbox.py:261-279`); claim/lease/heartbeat/dead-letter as described
  (`jobs/worker.py:56-158`); one worker processes its batch sequentially (`worker.py:66-67`), so
  concurrent detect jobs for one run only happen across workers.
- `RunQueries.summary_states` derives `processing | failed | current` from outbox rows of
  `SUMMARIZE_RUN` by dedupe key (`runs/queries.py:122-152`); generalising it by job type is a one-line
  parameter. `RunService.list_runs/get_run` call it per page (`runs/service.py:197,212`).
- Ingestion enqueues one coalesced summarize job per run with `summary_delay` (`store.py:175-186`);
  `summary_debounce_seconds` default 1.0 (`core/config.py:33`); worker lease 60 s, 5 attempts, backoff
  5 s to 300 s (`config.py:36-39`).
- `retry_call_ids` is iterative, memoised and cycle-safe (`cost/retries.py:26-47`); reusable for
  "retries caused by the failure" as the plan says.
- Canonical ordering: `sort_events` uses `(sequence, occurred_at, received_at, event_id)` when every
  event has a sequence, else `(occurred_at, sequence, received_at, event_id)`
  (`abb_event_schema/ordering.py:32-48`). See P2-1: the plan's SQL order does not match this.
- Spans: `derive_spans` sorts canonically, names fall back to `span.name, tool.name, shell.command,
  llm.model, file.path` (`spans.py:42-48`); content span kinds `shell/file/git/None` withheld
  (`runs/content.py:23,38-39`).
- Registry: `CONTENT_ATTRIBUTES` is the eight-key set the plan lists (`registry.py:150-161`);
  `loop.pattern` is a `STRING_LIST`, `loop.repetitions` an integer, `retry.of_event_id` a string
  (`registry.py:127-135`); `test_content_withheld.py:29-41` keeps `REVIEWED_METADATA` and fails for an
  unclassified string attribute (`:44-57`). Attribute strings are at most 4,096 characters and lists at
  most 64 items (`limits.py:7-8`), inline payloads at most 64 KiB (`limits.py:4`).
- Authorization: `holds()` is `authz/service.py:59-61`; every route needs a `RouteCase`
  (`tests/authz/registry.py:204-273`, runs group at 207-227); `test_cross_workspace.py` scans for the
  canary, ids and magnitudes 777.777 (`:41-50`); the world seeds `COMMAND_CANARY`, `PATH_CANARY`,
  `CUSTOM_CANARY` and `CWDCANARY` (`tests/authz/world.py:37-40,133-152`); `test_openapi.py:55` pins
  `seen == 36`; the 404-not-403 rule is `runs/service.py:36-44,304-307`.
- Cost rebuild: the HTTP route is bounded `le=MAX_REBUILD_RUNS` (`cost/router.py:152`) and audited
  (`:241`); the CLI `rebuild-costs` enqueues `run_ids(project, since, limit)` and audits `cost.rebuild`
  (`cli.py:537-545`). Note: the CLI's `--limit` is a default of 10,000, not a bound (`cli.py:163`); see
  P3-11.
- Tables: no findings table exists; `spans` and `cost_calculations` use composite FKs to `runs` with
  `ON DELETE CASCADE` (`tables.py:347-349,460-462`); `events` is `RESTRICT` (`:382-384`);
  `ix_runs_project_started (workspace_id, project_id, started_at DESC, id)` exists (`:321`);
  `ix_events_run_sequence` and `ix_events_run_time` exist (`:386-387`). The runtime role gets DML on new
  tables automatically through `ALTER DEFAULT PRIVILEGES` (`migrations/versions/0007_runtime_role.py:38-42`),
  so the plan's "covered automatically" claim for migration 0060 holds, and
  `test_runtime_role.py:99-118` asserts append-only tables are exactly `events` and `audit_log`.
- Migrations: the pinned list is `0001..0009, 0040..0047` (`tests/test_migrations.py:41-61`) and the
  `0044 -> head -> 0044` test exists (`:97`). An additive 0060 keeps it passing.
- Web: `jumpToError` (`RunDetail.tsx:213-221`) does what the plan says; `useRunEvents` pages 500 at a
  time with an infinite query (`lib/queries.ts:73-107`); `useRun` polls active runs (`:52-58`); the
  proxy allowlist regex is `server/upstream.ts:28-41`; `globals.css` is 897 lines; `RunsTable.tsx`
  shows errors and retries from `summary` (`:51-72`).
- E2E and CI: `analytics-e2e`, `coding-e2e`, `stream-e2e`, `auth-e2e` are CI jobs
  (`.github/workflows/ci.yml:108-268`) and Makefile targets; `cli add-member --role` and
  `create-session` exist (`cli.py:172-200`) and the scripts use them (`scripts/auth-e2e.sh:61-66`).
- Benchmark baseline numbers quoted by the plan match `docs/benchmarks/phase-7-regression-check.md`
  (30,000-event run p99 2,409-2,456 ms; freshness 1.2 s / 3.2 s).
- The two ASSUMED items: `pg_advisory_xact_lock(hashtextextended(...))` is **already used in
  production code** (`analytics/rollup.py:320-327`, per workspace-day) and exercised by the analytics
  tests in CI, so availability on the project's PostgreSQL is established by existing evidence; the
  plan should cite it instead of marking it ASSUMED. Lock independence: an INSERT into a table with a
  composite FK to `runs` takes `FOR KEY SHARE` on the run row, which is compatible with the
  summarizer's `FOR NO KEY UPDATE` and with ingestion's key-share (PostgreSQL row-lock matrix); so the
  detect job's writes do not wait on summarization, and nothing waits on the advisory lock but another
  detect job. The wording "no contact with the run row lock" is wrong (there is contact, it is
  compatible); correct the text. Runtime behaviour: UNVERIFIED (env).
- The allowlist approach (D8) is necessary, not just prudent: the SDK writes a sanitised `cwd` into
  `test.suite` (`packages/sdk-python/src/blackbox/coding.py:1361`), which `REVIEWED_METADATA` treats as a
  label. The plan's evidence keys exclude it; keep it that way.

## 2. Unverified

- Any runtime claim (advisory-lock interaction under load, the 3 s detector micro-benchmark on 50,000
  events, `make bench` deltas, migration 0060 up/down/up, E2E timing): UNVERIFIED (env), no `.venv`,
  `node_modules` or database in this worktree.
- "Detectors are O(n)": the sequence detector as described (for each n in 2..8, one pass) is O(8n) in
  tokens; the identical-request hash is O(total payload bytes) (up to 64 KiB x events); the cascade's
  sliding window O(n). Plausible, not measured.
- Percentile semantics of `cost_baseline` (nearest rank on Decimal) and the `>= 0.01` floor: reasoned
  only.

## 3. Findings

### P1 (must fix before implementation)

**P1-1. `EXPECTED_V1_OPERATIONS` is not in the plan; step 4 fails the registry completeness test.**
Plan: "Proposed design > API" and "Affected files" list `tests/authz/registry.py` (one case) and
`test_openapi.py` (37) but not `tests/authz/test_registry_is_complete.py:12`, which pins
`EXPECTED_V1_OPERATIONS = 31` and asserts it at `:102`. Scenario: step 4 adds the route and the
`RouteCase`; `uv run pytest tests/authz -q` (AC-4) fails on the count. Change: add
`test_registry_is_complete.py` (`31 -> 32`) to the hot-file list, step 4 and the Merge notes (Phase 16
already lists this file by name; Phase 11 must then land on 32 + 14 = 46, see section 4).

**P1-2. Client-controlled strings can exceed the 16 KiB evidence CHECK and dead-letter the job.**
Plan: D2 (`pg_column_size(evidence) < 16384`), D8 (`tool.name`, `llm.model`, `loop.pattern` tokens,
`tool_name` in `failures`, `alternates`) and KI-018 ("findings add no client-controlled unbounded
cardinality"). The truncation rule ("`tool.name` truncated to 128", "`loop.pattern` at most 20 tokens of
64 characters") appears only in security scenario 5, not in `model.py`'s contract or in any AC.
Scenario: a tenant's integration (or a hostile client with an `event.write` key) sends `tool.name` of
4,096 characters (`limits.py:7`); the cascade's `failures` list holds 20 of them (80 KiB) and the
sequence `pattern` holds 8 (32 KiB); the INSERT violates the CHECK, the handler raises, the job
retries five times with backoff and dead-letters; the run's findings are `failed` for an operator to
look at, and every re-summarisation re-enqueues the same failure. Change: make truncation part of the
`Finding` model (every string value <= 128 characters, every list bounded, `validate_evidence` also
asserts the serialised size < 15 KiB before the write, dropping the lowest-severity findings first if
needed) and add a unit test with 4,096-character names and 64-item `loop.pattern` lists (AC-2 or AC-1).
The same applies to `describe()` (titles and messages built from these labels).

**P1-3. The context-growth detector as defined flags most ordinary multi-turn runs.**
Plan: detector table, `context-growth@1.0.0`: "a non-decreasing stretch of >= 5 calls whose last value
>= 2.0 x its first and >= 20,000 tokens", severity `warning`. In a conversational agent every turn
appends to the context, so `llm.input_tokens` is non-decreasing by construction; a ten-step coding
run going from 8k to 30k input tokens meets the rule. Result: a `warning` on healthy runs, which the
health dimension `context` then turns into `warn` on nearly every run, directly against the mission
("developers must first trust that Agent Black Box accurately shows what their agents did") and the
spec's "grows monotonically beyond configured rate" (§81.1: a rate, not a ratio). Also the `healthy`
fixture, as described (no model-call growth), cannot catch this. Change: define growth as a rate
(for example tokens added per call over the stretch >= `findings_context_growth_min_delta_tokens`, or
the last value >= factor x first **and** the per-call increment itself increasing, i.e. the context
grows faster than linearly), keep `warning` only above an absolute size (for example >= 100,000
tokens) and make the lower band `info`; give the `healthy` fixture a normal monotonic context
(8k -> 24k over 8 calls) so AC-1 proves the rule does not fire on it.

**P1-4. Detector exceptions carry attribute values into `outbox_jobs.last_error` and the worker log.**
Plan: D1 ("a detector bug is not swallowed: the job fails and retries"), security scenario 7 ("no
evidence, payload, attribute value or message in any log line"). Code: `worker.py:134-158` stores
`f"{type(exc).__name__}: {exc}"` as `last_error` (truncated to 2,000 characters, `outbox.py:55-56`) and
logs with `exc_info=True` on dead letter; `jobs-list` prints `last_error`. A detector that raises on
a hostile value (`ValueError(f"bad pattern token {token}")`, a pydantic error echoing an evidence
value, a `KeyError('CMDCANARY...')`) puts captured content into the outbox table and the structured log,
where it is read by operators without `payload.read`. Change: the handler wraps each detector call and
re-raises a `DetectorError(detector_name, event_count)` with a fixed message, `from None`
(so the traceback printed by `exc_info` does not include the original message); the log-hygiene test
of AC-3/scenario 7 asserts that a detector raising with the canary in its message leaves no canary in
`caplog` or in `outbox_jobs.last_error`.

**P1-5. Health reports `ok` for dimensions that have not been analysed.**
Plan: D9 `loops` ("none: ok") and `context` ("by the context-growth finding's severity") are
functions of the findings only; the route's `state.status` can be `none` (every run summarised before
Phase 10, until `rebuild-findings`), `processing`, `outdated` or `failed`. Scenario: an OWNER opens a
run from last week: "Loops: ok", "Context: ok", `overall: ok`, with no detector having run; after a
`DETECTORS_VERSION` bump the same page says `ok` from stale rules. Change: `health(facts, findings,
state)` returns `no_data` for `loops` and `context` (and the cost-spike part of `cost_efficiency`)
unless `state.status == current`, with the rule sentence saying why ("not analysed yet" /
"findings outdated"); AC-6 gains those cases.

**P1-6. AC-3's equality is not well defined for findings that depend on data outside the event set.**
Plan: AC-3 ("stored findings equal a from-scratch `run_all` over the complete event set by
`(detector, finding_type, fingerprint, evidence)`"). Two inputs of the job are not functions of the
event set: (a) `RunFacts` is read from the run row (D1), which can be older than the events read in the
next statement (READ COMMITTED: a summarizer commits between the two reads), so `status`, `cost_usd`
and the counters may describe a smaller event set than the detectors see; (b) the cost-spike
baseline moves (D5 admits this). Scenario: the test ingests the `failing` run in random batches with
worker interleaving; a detect job runs with the row still `RUNNING` while the events contain
`run.failed`; `failure-chain` fires by the event rule, `cost-spike` does not (terminal only by the
row), the next job flips it; the "equals from scratch" comparison is order-dependent and flaky, or
passes only because the last job happened to see a consistent pair. Change: (1) take status, counters
and completion from the loaded events (fold `_status_after` as `derive_run` does, which is cheap next
to the load) and use the run row only for cost and project/agent; (2) read the run row **after** the
events, inside the same transaction, and record `summary_version_seen`/`event_count` from it so a
mismatch is visible; (3) in AC-3 compare everything except `cost_spike`, and test `cost_spike`
separately with a frozen baseline (AC-8). State in D1 that detect results are a function of
(event set, run cost at the time, baseline at the time).

### P2 (should fix)

**P2-1. The capped load is not the canonical prefix.** Plan D3: `ORDER BY sequence, event_id` or
`ORDER BY occurred_at, event_id`, then `sort_events`. Canonical order is `(sequence, occurred_at,
received_at, event_id)` or `(occurred_at, sequence, received_at, event_id)` (`ordering.py:32-37`).
With ties on `sequence` (Phase 9 D4: several emitters in one run, remote ones without `sequence`) or
on `occurred_at`, the first 50,000 by the plan's key are not the first 50,000 canonically, and the
stored `ordering_mode` may be stale relative to the events just read (`summary.py:175` flips it to
`time` when any event lacks a sequence; the row is updated only by the next summarisation). A subset
that includes some later events and omits some earlier ones produces patterns that are not in the
run. Change: order by the full canonical key in SQL (both indexes `ix_events_run_sequence` and
`ix_events_run_time` lead with `(workspace_id, run_id)`, the extra columns cost a sort of the capped
set only), decide the mode from the row but say so in `run_findings` (`ordering_mode_seen`), and make
AC-9's truncation test use ties.

**P2-2. Multi-agent runs (Phase 9) interleave emitters and break the sequence and context detectors.**
Phase 9 D1/D4: one run, many `agent_id`s, time ordering across emitters. Two agents alternating
`search -> read` and `plan -> write` yield a merged token stream with no repetition of length <= 8 or,
worse, a spurious one; `llm.input_tokens` of two agents interleaved is never "non-decreasing". Change:
detectors partition tokens and series by `event.agent_id` (already on every event, no ordering
assumption) and report `agent_id` in evidence (a slug, metadata); add a two-agent fixture to AC-1
(security scenario 10 already lists "events of two agents in one run" but no detector rule addresses it).

**P2-3. `hash_prefix` is an equality oracle on withheld payloads.** Plan: identical-request evidence
exposes 12 hex characters of an unsalted SHA-256 of the canonical payload to every actor with
`run.read`, including VIEWER. A viewer who can guess the payload of a tool call (`{"path":
"/etc/passwd"}`, a known prompt) confirms it offline against the 48-bit prefix; the same prefix also
links identical payloads across runs, projects and, for a dual member, workspaces (no per-workspace
salt). The spec (§81.1) hashes "after redaction" for grouping, not for display. Change: drop
`hash_prefix` from the evidence (the group itself, its `count` and `event_ids` carry the finding) or
show it only to actors with `payload.read`, which would reintroduce a per-actor projection the plan
rightly wants to avoid; dropping is simpler. Update security scenario 2 accordingly.

**P2-4. A fingerprint collision inside one run dead-letters the whole job.** D2: `uq_findings_run_fingerprint
(workspace_id, run_id, fingerprint)` and `fingerprint = sha256(detector | finding_type |
start_event_id | key)`. Two findings of one detector with the same start event and key are possible
(for example two `probable_cause` findings for the same `cause` and `tool_name` whose chains start at
the same event, or two `identical_request` groups that differ only in `event_type` if the key omits
it) and the plan has no de-duplication before the INSERT, so the uniqueness constraint turns a
detector's duplicate into a five-attempt failure and a dead letter. Change: `run_all`
de-duplicates by fingerprint (keep the higher severity), and a unit test inserts two equal-fingerprint
findings through the repository and expects one row, not an exception.

**P2-5. The VIEWER parity E2E demotes the only owner.** Plan "E2E": "a VIEWER session (role downgraded
through `PATCH /v1/members`)". The script creates one dev owner via `add-member --role OWNER` and
`create-session` (as `analytics-e2e.sh:85-86`); demoting the last owner is refused with `409 LAST_OWNER`
(`workspaces/members.py:193,216`). Change: `cli add-member --workspace ... --email viewer@local.test
--role VIEWER` and a second `create-session`, as `auth-e2e.sh:63-66` does for a DEVELOPER.

**P2-6. AC-11's budget is not enforceable as written.** "1/4/8-client p95 within +-10%" and "p99 of the
30,000-event run not more than +20%" against a baseline whose own repeats vary more than that: Phase 7
recorded 8-client p95 of 268.9, 324.2 and 330.0 ms (+-10% exceeded by noise alone) and 4-client p99 of
185.0-207.9 ms. The criterion will fail or pass by luck, and the plan's remedy ("tune
`ABB_FINDINGS_MAX_EVENTS` or the delay clamp") cannot fix noise. Change: compare the median of three
runs before and after on the same VM within one session; treat the 30,000-event run's detect
`duration_ms` and the worker's share of CPU (or the summary-freshness delta) as the primary measure of
this phase's cost, since ingest p99 measures the summarizer, not the detector; record the raw triples.

**P2-7. Patterns of length 1 are excluded.** Detector table: "pattern length n in 2..8". The most common
loop (the same tool called again and again with different arguments: `tool:read` x 30, or `llm` x 30
with no tool between) is never reported by `repeated-sequence`, and `identical-request` only sees it
when payloads are captured (opt-in, INV-5). Change: allow n = 1 with a higher threshold
(`findings_loop_min_repetitions_single`, say 8) or state the gap in the runbook and the KI list.

**P2-8. The runs-list badge and findings route are added to every page; the findings state query is
per page but the badge needs `run_findings` plus the outbox state.** `RunOut.findings` ("count,
max_severity, state") needs `badges(run_ids)` and the detect job's `summary_states` equivalent: two
more queries per `GET /v1/runs` page (today one: `summary_states`). The list is the dashboard's hot
read (`useRuns` polls every 15 s). Change: make `state` on the badge optional and derive it from
`run_findings.detectors_version` only (`none | current | outdated`), leaving `processing | failed` to
the per-run route; or one query joining `run_findings` to the outbox by dedupe key. Measure in AC-11
(`GET runs list (limit 50)` is already in `make bench`).

**P2-9. `findings.id` changes on every rewrite.** D2: "`id` is a fresh `fnd_` ULID per write". A run
that is active is rewritten every delay window; the UI polls every 3 s; React keys, deep links and
any later comment or dismissal keyed on `id` churn, and `created_at` is reset so "first seen" is lost.
Change: derive the id deterministically from the fingerprint (UUIDv5 over `workspace_id:run_id:
fingerprint`, still presented as `fnd_...`), or make `fingerprint` the public id and keep `id`
internal; keep `created_at` as first-seen by `INSERT ... ON CONFLICT (workspace_id, run_id,
fingerprint) DO UPDATE` instead of delete + insert (still idempotent and derived).

**P2-10. Order-independence claim versus `retry-storm`'s time window and `cascade`'s call window.**
"Every detector is order-independent (it sorts nothing itself; the context is canonically ordered
once)" is true only if a window is defined over canonical order and `occurred_at`. "Within 60 s"
over a sequence-ordered run whose clocks are skewed between emitters (Phase 9) is not canonical.
Change: define the window over `occurred_at` of the events in canonical order and state that under
clock skew it is best effort; the shuffle test in AC-1 cannot catch this (it permutes arrival, not
clocks), so add a fixture with skewed `occurred_at` across two emitters.

**P2-11. Stale findings after a dead letter are undefined.** Plan `FindingsService.get`: `failed` from
the outbox, but `findings` rows from the last successful job remain. Does the route return them with
`state.status == failed`? The UI text ("Analysis failed and needs an operator") next to a list of
findings from an older event set is misleading. Change: return the rows with `state.computed_at` and
`event_count_seen` and have the UI say "showing findings from <time> over N events; a newer analysis
failed"; test the case in AC-5.

**P2-12. `POST /v1/cost/rebuild` and the Phase 16 backfill now also re-run every detector.** D1:
"the summarizer always enqueues". A 10,000-run cost rebuild (or Phase 16's documented search backfill
through `POST /v1/cost/rebuild`) doubles the worker's work and reads every event set twice. Correct
by INV-2 but not in the plan's cost model or the runbook. Change: say so in the runbook and in
`docs/RELIABILITY.md`; consider skipping the detect enqueue when `summary_version` and the run's
`event_count` are unchanged (the row already carries both), which also removes the second read for
no-op re-summarisations.

### P3 (optional)

- **P3-1.** D1 wording: "no contact with the run row lock" -> "takes only a key-share lock through the
  FK, compatible with `FOR NO KEY UPDATE`"; cite `analytics/rollup.py:327` for the advisory-lock pattern
  and note the shared 64-bit advisory keyspace (`analytics:` and `findings:` prefixes; a collision only
  serialises two unrelated jobs).
- **P3-2.** Consider `pg_try_advisory_xact_lock` and, when it is held, re-enqueueing (coalesced) and
  returning instead of blocking a worker slot for the duration of another worker's job on the same run.
- **P3-3.** `cost_baseline` in `findings/repository.py` reads the `runs` table: by AGENTS.md's module rule
  it belongs on `RunQueries`/`RunRepository` (runs module) and is called by the findings service.
- **P3-4.** `summary_states(job_type=...)`: add a sibling `job_states(run_ids, job_type)` and keep
  `summary_states` delegating, to avoid signature churn in a file Phase 16 rewrites.
- **P3-5.** `reliability` dimension has no stated rule (D9 lists its inputs only); define the
  boundaries like the other dimensions.
- **P3-6.** `failure-chain`: define "root error event" (first error on the terminal event's span
  lineage in canonical order?), "last known-good action" when none exists, and the mutation set
  (`file.created|modified|deleted`, `git.commit|push`; not `file.read`, `git.diff`).
- **P3-7.** `context_overflow` regex `context|token|length` on `llm.error_type` matches
  `invalid_token`/`authentication_token`; anchor the vocabulary (`context_length`, `max_tokens`,
  `too_long`) or match whole words.
- **P3-8.** `retry-storm`: ">= 5 retries in the run" with no time bound calls a three-hour run with five
  spaced retries a storm; keep the absolute rule but at `info` unless the burst rule also fires.
- **P3-9.** Severity stacking: one incident (three failed calls, retried, then the run fails) yields
  `retry_storm` + `identical_request` + `tool_failure_cascade` + `failure_chain` + `probable_cause`;
  the badge says "5 findings, critical". Consider linking them by the shared span in evidence and
  counting the badge by distinct `start_event_id`.
- **P3-10.** Fingerprint includes `start_event_id`: a late-arriving earlier event extends a stretch
  backwards and changes the fingerprint (a future dismissal keyed on it is lost). Acceptable; document
  it in ADR-080 so the dismissal design does not assume stability under late arrival.
- **P3-11.** "Cost rebuild pattern: bounded to 10,000" is true for the route (`le=`), not the CLI
  (`cli.py:163`, a default). AC-10's "refuses `--limit 10001`" needs explicit argparse validation; add
  the same to `rebuild-costs` or say the two differ.
- **P3-12.** Baseline hygiene: exclude runs never summarised (`summary_version = 0`, cost 0) and note
  that an insider with `event.write` can move the baseline either way (cheap runs make the next normal
  run a "spike"; expensive ones hide one). Impact is a false finding, not exposure; say so in the
  security note.
- **P3-13.** "Show evidence" for an event outside the loaded pages (30,000-event runs page 500 at a
  time): open the drawer from `useEventDetail(runId, eventId)` directly rather than failing soft; the
  detail route already withholds content per actor.
- **P3-14.** Steps versus files: `FindingsRepository` is in step 2's title but `repository.py` is listed
  under step 4; `findings/schemas.py` appears in step 4 but not in "Affected files > New". Align.
- **P3-15.** Severity flaps on active runs: a legitimately repetitive run is `warning` while running and
  `info` once it succeeds (D7). Say so in the UI state text ("severity may change until the run ends").
- **P3-16.** `summary_version_seen` has no stated consumer; either use it (`outdated` when the summary
  version moved, so a Phase 9/16 bump re-runs detectors by rebuild) or drop the column.

## 4. Cross-plan conflicts and what Phase 10 must change (merge order 9, 10, 16, 11)

(a) **`SUMMARY_VERSION`.** Phase 9 sets it to 3 (`phase-9 plan:199`); Phase 16 sets it to 3
(`phase-16 plan:229`) and says "the merge takes the higher number" (`:413`), which is wrong when both
chose 3: after Phase 9 merges, Phase 16 must bump to **4**, or runs summarised under Phase 9's rules
would never be re-indexed. Phase 10 does not touch it (correct). Phase 10 must: (1) not depend on the
value, only record `summary_version_seen` (P3-16); (2) add one line to its Merge notes stating that
every `SUMMARY_VERSION` rebuild (`POST /v1/cost/rebuild`) now also re-runs detectors (P2-12), so the
Phase 16 backfill runbook should mention worker load.

(b) **Route counts.** Today: `seen == 36` (`test_openapi.py:55`), `EXPECTED_V1_OPERATIONS = 31`
(`test_registry_is_complete.py:12`). Phase 10 adds one route: **37 / 32** (P1-1 adds the second
number to the plan). Phase 16's default (`GET /v1/runs?q=`) adds none; its alternative claims 37/32 as
well (`phase-16 plan:120`), which collides; Phase 16 must rebase to 38/33 if that alternative is
taken. Phase 11 claims `36 -> 50` (`phase-11 plan:296,353`) and must land at **51 / 46** after Phase 10.
Phase 10 must state the pair `37 / 32` in its Merge notes so the later phases compute from it.

(c) **Migrations.** Phase 9 adds none; Phase 10 `0060` from `0047` (first to merge: no rebase);
Phase 16 `0080` must then set `down_revision = "0060"` and append `"0080"` after `"0060"` in
`test_migrations.py`; Phase 11 `0070` must set `down_revision = "0080"` with `0071` following, and
its new `test_a_database_at_0047_upgrades_to_head_and_back_to_0047` still holds (all additive). Phase 10's
Merge notes already describe the rule; add the resolved chain `0047 -> 0060 -> 0080 -> 0070 -> 0071`
so no phase re-derives it.

(d) **`jobs/handlers.py::summarize_run`.** Phase 16 D7 inserts an index upsert right after
`apply_derivation` (`handlers.py:52`, "one call", `phase-16 plan:228`); Phase 10 inserts the detect
enqueue; Phase 11 adds a handler to `HANDLERS` and says "neither hooks `summarize_run` in this round"
(`phase-11 plan:467`), which is false for Phase 10 (D1 hooks it). Phase 10 must: put its enqueue as the
**last statement of the `if events:` block** (after the analytics loop, `handlers.py:57-62`), in a
small helper `_enqueue_detect(outbox, tenant, run_id, event_count)` so the diff is a two-line append,
and add `DETECT_FINDINGS: detect_findings` as the last entry of `HANDLERS`; tell Phase 11 in its
Merge notes that the hook exists and where a future auto-evaluation enqueue goes (after it). Phase 16's
insertion sits in a different hunk (after line 52) and merges cleanly if Phase 10 appends at the end.

(e) **Shared hot files.** Beyond the plan's list: `tests/authz/test_registry_is_complete.py` (P1-1);
`runs/queries.py` (Phase 16 rewrites the listing; Phase 10 should add a method, P3-4); `RunOut`
(Phase 10 `findings` "placed last", Phase 16 `matched` also last: trivial but both plans claim the
last slot; Phase 10 should say "after `updated_at`" and Phase 16 after that); `RunDetail.tsx` (Phase 9
view switch and agent filter, Phase 10 panel and `jumpTo`, Phase 11 evaluations section): Phase 10
should render `<Findings />` from a single insertion point and keep `jumpTo` as a new function that
`jumpToError` calls, not a rename; `globals.css` append-only by 10, 16, 11 (each later phase rebases a
trivial EOF conflict); `packages/event-schema/ids.py` (`IdKind.FINDING`; Phase 11 may add evaluator or
experiment kinds: additive lines, regenerate schema artefacts after merge); `docs/KNOWN_ISSUES.md`:
Phase 16 already names KI-081; Phase 10 files "new items at completion" without numbers: reserve a
range now (for example KI-085..089) to avoid duplicate ids across the four phases; `core/config.py`
(Phase 10 `findings_*`, Phase 16 `search_*`): append-only, fine.

(f) **Evidence convention.** Phase 11 says both phases "store `evidence_event_ids` as a JSONB array of
`evt_` ids validated against the run" (`phase-11 plan:466`). Phase 10 stores `start_event_id`,
`end_event_id` columns plus per-type lists inside `evidence`. Either Phase 10 adopts an
`evidence_event_ids` array column (cheap, and it makes "evidence links resolve" checkable by one
query) or Phase 11's note is corrected; decide before either lands.

## 5. Mutation checks the plan should add (each must fail at least one test)

- Replace `tool.name` in evidence with a 4,096-character string (P1-2): size test fails.
- Make a detector raise `ValueError(COMMAND_CANARY)` (P1-4): log-hygiene and `last_error` tests fail.
- Return `ok` for `loops` when `state.status != current` (P1-5): AC-6 fails.
- Order the capped load by `(sequence, event_id)` only (P2-1): the tie fixture fails.
- Feed a two-agent interleaved fixture to the sequence detector (P2-2): expected findings missing.
- Insert two findings with one fingerprint (P2-4): repository test expects one row, no exception.
- Delete the `DetectorError` wrapper (P1-4) or the `validate_evidence` size assertion (P1-2).

## 6. Disposition requested

Fix P1-1..P1-6 in the plan text (and the fixtures they imply) before `implement-change`; decide P2-3
(drop `hash_prefix`) and P2-9 (stable ids) now because they change the contract; the rest can be
addressed in the plan or filed as KIs with the trigger stated.
