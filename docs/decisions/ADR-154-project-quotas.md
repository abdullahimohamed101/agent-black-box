# ADR-154: Per-project quotas that refuse loudly

Status: Accepted (Phase 19a, step 3). Implements D2 and gate 3 of the Phase 19a plan. Resolves KI-018, the quota half of KI-040, and narrows KI-055.
Date: 2026-10-14
Phase: 19a

## Context

A project's agents, runs, events and artifacts were bounded only by the per-second rate limit and, for one artifact, by its size (KI-018 S1, KI-040,
KI-055). A runaway agent or a hostile key could therefore grow storage and noisy lists without limit, as slowly as it liked. The plan (D2) settled the
answer with the user: **refuse loudly at a limit; never silently fold or drop accepted run data.**

## Decision

1. **Four quotas per project, with defaults and overrides.** `agents` (distinct agent slugs ever seen, 500), `runs_per_day` (runs created per UTC day,
   50,000), `events_per_day` (events accepted per UTC day, 5,000,000) and `artifact_bytes` (total stored artifact bytes, 5 GiB). The per-run event cap
   and the per-artifact size cap stay as they are. The vocabulary is a closed enum (`abb_api.quotas.model.Quota`). Defaults come from `ABB_QUOTA_AGENTS`,
   `_RUNS_PER_DAY`, `_EVENTS_PER_DAY`, `_ARTIFACT_BYTES`; an owner or admin overrides each one per project (`project_quota_overrides`, nullable columns, composite
   foreign key to `projects`, so INV-3 holds and deleting a project removes its overrides). `ABB_QUOTAS_ENABLED=false` is an emergency switch and the benchmark's
   baseline: nothing is checked or counted while it is off. `ABB_QUOTA_LOCK_TIMEOUT_SECONDS` (5) bounds the wait described in point 5.

2. **What a refusal is.** A refused item gets the fixed code `QUOTA_EXCEEDED` and the closed quota name.
   * `POST /v1/events/batch`: per event inside the normal `202`, counted in `rejected`, exactly like `RUN_PROJECT_MISMATCH` or `REDACTION_FAILED`:
     `{"index", "event_id", "code": "QUOTA_EXCEEDED", "quota": "events_per_day", "issues": [{"loc": ["quota"], "code": "events_per_day", "message": ...}]}`.
     The only contract change is the optional `quota` field on `EventErrorOut` (additive, documented in `api-v1.md`); an old client sees an ordinary rejected event.
     The message is a fixed sentence and never repeats anything the client sent.
   * `POST /v1/events`, `POST /v1/runs` and `PUT /v1/artifacts/{id}`: `413 QUOTA_EXCEEDED`, `details = {quota, limit?, used?}`, not retryable. Why 413: the existing conventions
     are 413 for "this is more than may be stored" (`PAYLOAD_TOO_LARGE`) and 429 for "slow down and retry"; a quota is the first kind, and a 429 would make every client
     (the SDK included) retry something that cannot succeed, while a non-429 4xx is "do not retry" to every generic client. The error category is `RATE_LIMIT` (the nearest existing
     one; adding a category would change the published error enum for no gain).
   * The Python SDK needs no change to be safe (see "SDK" below).

3. **What is refused, and what never is.** Evaluated per event in batch order, after replays are set aside (`abb_api.quotas.model.admit`, a pure function):
   an event naming an agent slug the project has not seen is refused when the project is at its agent limit; an event of a run the project has not seen is refused at the
   run limit (every later event of that run too, in this and later batches); any other event is refused when the day's event count is used up. A refused event costs nothing:
   no agent row, no run row, no count. Everything the project already has keeps working: events of existing runs and agents are accepted at any usage of the agent and run
   quotas, and **a replayed event is never refused and never counted twice**: events already stored are classified `duplicate`/`conflict` by their content hash *before* any quota
   is consulted, and a batch of nothing but replays only reads (it takes the lock for the length of one read and stores nothing). Events for a run that belongs to another project stay
   `RUN_PROJECT_MISMATCH` (decided before quotas). Lowering a limit below current use refuses new items only.

4. **Counters: transactional tables, not the shared limiter and not scans.** The shared limiter (ADR-152) was the first candidate for events per day, since its windows are
   database-clock fixed windows and a day window aligns to UTC midnight. Rejected, for four reasons: it reserves *before* the outcome is known, so a replay or a rolled-back batch would count
   (the plan says replays never count twice); it fails open, which is the wrong failure for a limit whose point is to refuse; its windows expire (a total such as agents or bytes is not a window); and its exactness
   is "at most the limit per window" rather than "equals what committed". Instead, migration 0098 adds `project_usage_daily` (runs created and events accepted, one row per active project per UTC
   day) and `project_artifact_usage` (total stored bytes), both keyed by `(workspace_id, project_id[, day])` with composite foreign keys. They are written **in the transaction that stores the data**,
   from what was actually inserted (`INSERT ... RETURNING` for runs and events), so each counter equals what committed, by construction, and a rolled-back batch counts nothing.
   Distinct agents is not a counter at all: `count(*)` over the unique index `(workspace_id, project_id, slug)`, bounded at the limit, and only when a batch contains a slug the project does not have (a
   normal batch does one indexed existence lookup). The alternatives were a full scan per request (rejected: cost) and a maintained agent counter (rejected: a second source of truth for something the table already
   says exactly). Days are UTC dates taken from the service clock (the same clock that stamps `received_at`); the usual caveat applies that processes whose clocks differ by seconds place a batch near midnight on
   either side, which moves at most the events of those seconds to the neighbouring day's count.

5. **Concurrency: exact, by one lock.** Every writer of counted data (batch ingestion, `POST /v1/runs`) takes the project row `FOR NO KEY UPDATE` before it reads a counter and holds it until commit; the
   counters are then read in a *separate* statement (a joined `SELECT ... FOR UPDATE` that waited would re-check only the locked row and hand back stale usage rows: an early version of this change did, and the
   race test caught it). So two concurrent batches at a limit are served one after the other, and **no quota can be exceeded at all** by a race: the stated bound is zero, for agents, runs, events and bytes (tested with 10 to 16
   concurrent batches against each limit). `FOR NO KEY UPDATE` does not block the key-share locks foreign keys take, so unrelated inserts into the project's children are unaffected. The wait for the lock is bounded
   (`lock_timeout`, 5 s by default, local to the transaction): a batch that cannot get it is answered `503 DEPENDENCY_UNAVAILABLE` with `Retry-After`, which SDKs retry, rather than queueing behind a stuck holder
   forever. Artifact bytes use one conditional upsert instead (`ON CONFLICT DO UPDATE ... WHERE bytes + n <= limit`): the counter row is its own lock, and an upload whose bytes do not fit rolls back its artifact row
   with it (tested with eight concurrent uploads against room for two). The price is stated in KI-178: ingestion of one project is serialised batch by batch.
   Measured cost in `docs/benchmarks/phase-19a-quotas.md` and in Consequences below.

6. **Artifacts.** The check uses the *stored* form (after server redaction), reads the counter once before the file is written (so an over-quota upload costs no write), and decides authoritatively in the
   transaction that records the artifact (point 5). A retry of an artifact that is already stored is a `200` even at the limit (its id is looked up first, as before). The file of a refused upload is removed unless a concurrent
   upload of the same artifact id kept it. The limit used by the in-transaction check is the one read before the write, so an override changed in that instant applies to the next upload.
   The counter is backfilled by the migration from `artifacts`, so existing data counts from the first request.

7. **Overrides: who, how, audit.** `PUT /v1/projects/{project_id}/quotas` needs `project.write` (owners and admins; keys and other roles 403; a project of another workspace or a bad id 404, one body). A body field left out stays,
   a number sets the override, `null` removes it; each number is range-checked against the same bounds as the table's CHECK constraints (422 `QUOTA_SETTINGS_INVALID`, never echoing the value). Each changed quota writes
   one audit entry in the same transaction: action `project.quota.update`, resource the project, details exactly `{quota, old, new}`; a request that changes nothing writes none. `GET` (the same path) needs only `project.read`
   (every role; a project-bound key sees its own project) and returns limit, default, whether overridden, use, percent and a status (`ok`, `near_limit` from 80 percent, `at_limit`) per quota. The route registry and
   authorization matrix carry both routes.

8. **Visibility.** Settings > Quotas (a new tab for everyone who reads projects; the Security tab needs `project.write`, so usage could not live there). Each quota shows use, percentage, a bar and a status as a
   glyph plus words, amber from 80 percent and at the limit, and an at-limit quota states in words what is refused and until when; red stays reserved for a failed run (ADR-111), no new colour, nothing animates. Owners and admins
   edit overrides (storage in MiB, empty = default, validated before sending). No alert yet (KI-179).

9. **SDK.** A per-event refusal arrives inside a `202`, so the SDK already treats it as it treats every per-event rejection: no retry, no exception into the host, counted in `rejected_by_server`, one warning a minute
   (`packages/sdk-python/tests/test_exporter.py::test_quota_refusals_inside_a_202_are_dropped_counted_and_never_retried`). A `413` on an artifact upload is non-retryable in the SDK (one attempt, `dropped_artifacts`).
   No SDK source change and so no version bump (a bump touches six integration lock files); an additive `dropped_quota` counter is KI-179.

## Rollups and cardinality (KI-055)

Rollup rows are keyed by project, UTC day and a few folded dimensions (200 names a day, 2,000 rows per table, ADR-043). The quotas now bound two of the three multipliers: agents per project (500, so the name dimension
never has more to fold than the quota allows) and runs created per day (50,000, so a project's day dimension grows by at most that many *claimed start days* per day). The third is not bounded: the day of a run is its client-claimed
start time, so a client can place its allowed runs on many distinct days, and nothing prunes rollups. Rollup storage is therefore linear in the number of runs a project may create, at a few rows each, with no unbounded burst
and no growth in time alone. Whether to clamp the claimed day or add rollup retention is a Phase 19b decision (an event-schema change needs its own ADR); KI-055 stays open at S3 with that target. Rollups were not redesigned.

## Consequences and stated limits

* The cost is measured, not assumed (`docs/benchmarks/phase-19a-quotas.md`, one laptop, shared with other work): one exporter sending 100-event batches pays **+3.7 ms p50 (+8 %)** per batch; four exporters of one project, being serialised on the lock, see p50 +36 % and 24 % less throughput (about 2,900 events/s of one project, a little above the default 2,000 events/s rate limit); ten-event batches from eight exporters lose 36 % (about 1,400 events/s). A batch of replays is 40 % faster than before. The cost is KI-178 and is the price of an exact, race-free limit; `ABB_QUOTAS_ENABLED=false` removes it.
* Statement count is the lever on the hot path: a decision is one `set_config`, one lock and one combined read (events, runs and agents that exist, overrides, the day's counters), then one counter upsert; the first version used six statements for the same decision and cost 12 ms instead of 3.7.
* Counting started at migration 0098: the first day's events and runs exclude earlier activity (artifact bytes were backfilled). Switching quotas off stops the counters; switching back on resumes from stale figures (OPERATIONS).
* Not counted: events the server writes itself (decisions and approvals, bounded by their own budgets) and nothing is decremented on deletion (nothing deletes artifacts or runs yet; Phase 19b must decrement `project_artifact_usage`).
* A raised limit is a lever on the operator's resources, so it is owner/admin only, audited, and range-checked; the defaults are abuse rails, not capacity guidance.
* `POST /v1/runs` shares the runs-per-day counter, so queued runs and event-created runs count together.
* Usage rows are one per active project per day and are not pruned (KI-180).
* Not fixed (review note): `artifacts/service.py` reads the artifact-bytes limit before the quota transaction, so one upload can exceed a limit that an owner lowered a moment earlier; the counter itself stays exact.
* Least privilege (migration 0100): the application role cannot DELETE from the three quota tables; it only upserts them.

## Alternatives considered

* **Fold or drop the excess** (the old "(other)" idea): rejected by D2; data that was accepted must stay true, and a silent drop is the failure mode this product exists to expose.
* **Shared-limiter counters** (point 4) and **reserve-then-refund** in a separate short transaction (no lock, but an inexact figure after a crash and a refund path to get right): rejected for exactness.
* **A scan per request** for agents or bytes: rejected for cost; **a trigger-maintained counter**: a second mechanism where the ingestion transaction already has the numbers.
* **429 for refusals**: rejected in point 2.
