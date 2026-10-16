# ADR-160: Retention enforcement: whole runs, in a fixed order, resumable, with a visible `trace_state`

Status: Accepted (Phase 19b, step 2). Implements the deletion half of ADR-159 (section 4 there is superseded by this record). Resolves KI-021, KI-040, KI-057, KI-072, KI-103, KI-180, KI-184; narrows KI-055.
Date: 2026-10-15
Phase: 19b

## Context

ADR-159 built the role, the policy, the dry run and the reports; `enforce` was refused. This step turns `ABB_RETENTION_MODE=enforce` into real deletion. The hard parts are not the `DELETE` statements but what a reader sees while they run, what happens when the process dies half way, and making sure a deployment that gets something wrong (a wrong artifact directory, a typo in a policy) loses as little as possible.

Settled with the user earlier (D2/D3): tiers by whole run, run end time against the workspace policy; dry run is the default; enforcement is bounded, resumable and idempotent, marks a run `deleting` first, records completion per backend, writes counts-only audit rows. Everything below that is not in D2/D3 was chosen here and is marked **(chosen)**.

## Decision

### 1. One procedure per run and tier, a fixed order, completion per backend

For every (workspace, tier) the job selects expired runs oldest first (`RUN_ENDED_AT < cutoff`, the clamped end of ADR-159), at most `ABB_RETENTION_MAX_RUNS_PER_TICK`, and runs the tier's procedure for each. A row in **`retention_run_progress`** (migration 0102) exists from the first step until the last, with a flag per backend:

| flag | backend | what it covers |
| --- | --- | --- |
| `artifacts_done` | artifact store | the files (through `ArtifactStore.delete`, INV-7; never a raw path), then the rows and the quota counter |
| `search_done` | search index | the run's `run_search` row |
| `detail_done` | Postgres detail | events, spans, cost lines, findings, run findings, evaluations, run-bound jobs, in batches; then the last rows, the `expired` mark and the tombstone in one transaction |
| `record_done` | Postgres record | the run row (cascading what hangs off it) and its tombstone, one transaction |
| `analytics_done` | rollups | the day's rollups rebuilt from what is left |

Order per tier (a step already done is skipped, an unfinished one is repeated; every step is idempotent):

* **artifacts tier**: artifacts.
* **trace tier**: artifacts, mark the run `deleting`, search entry, detail, analytics.
* **run tier**: the trace steps if not done, the record, analytics.

The tiers of one workspace run in that order within a tick, so by the time the run tier acts the trace tier has normally finished; the run tier does the trace steps itself only when a bound stopped the earlier pass. When all five flags are true the progress row is deleted; the **tombstones** (`retention_tombstones`, one per run and tier: `artifacts`, `trace`, `run`) are the permanent record. A tombstone is written **in the same transaction as the last delete of its tier** (the final artifact batch; the final detail transaction; the record delete) and with `ON CONFLICT DO NOTHING`, so a rerun never duplicates one. An artifacts tombstone exists only for a run that had artifacts.

**The mark comes after the artifact step (chosen).** `trace_state = deleting` is set when the job is committed to removing the detail, i.e. after the artifacts are gone. If the artifact store is down the run keeps reading `available` (events still there) instead of sitting in `deleting` for as long as the outage lasts.

**The rollup is rebuilt after the data it aggregates is gone (chosen; the order given in D3 listed it before the record).** Rebuilding before the record is deleted would keep counting the run. The procedure therefore ends with the rebuild, once per affected day (the run's UTC start day, kept in the progress row because it must outlive the run record), through the existing `refresh_day`. Consequence, accepted and recorded as KI-199: after the trace tier the run no longer contributes to the span, latency and cost-by-model rollups of its day (they aggregate the deleted tables) while the runs rollup (typed totals on the run record) still counts it until the run tier.

### 2. What a reader sees: `runs.trace_state`

Migration 0102 adds `runs.trace_state` (`available` | `deleting` | `expired`, a CHECK constraint, a partial index on `deleting` for resume). `GET /v1/runs` and `GET /v1/runs/{id}` return it (additive field). `available` to `deleting` happens in one transaction at the mark, `deleting` to `expired` in the same transaction as the last detail rows and the tombstone: a reader never sees `expired` with events left, nor `available` with events missing. While `deleting` the events endpoint may return fewer events than the summary says; the field is the signal. After `expired` events, spans and findings are empty and the findings badges read "not analysed" (their rows are gone), the record (summary, cost, timings, metadata) is untouched. Comparing or evaluating an expired run answers `409 TRACE_EXPIRED` instead of a comparison of nothing. After the run tier the run is a `404`. The UI (step 3) renders "trace expired" from this field.

### 3. Batches, bounds and the kill switch

* A **batch** is one short transaction with a statement timeout, `ABB_RETENTION_BATCH_SIZE` rows (default 2000), deleted by `ctid` of a bounded sub-select (so exactly the picked rows go). Tables the role reads only by column (`sessions`) are batched by primary key.
* Before **every batch** the job calls `Control.checkpoint`: it stops (raising `Halt`, no half transaction) when the process was told to stop (SIGTERM/SIGINT), when the mode is no longer `enforce`, when the tick's time budget is spent (`budget`), or when the pass deleted `ABB_RETENTION_MAX_ROWS_PER_TICK` rows (`rows`: only this pass ends). A run left half done stays `deleting` with its progress row and the next tick resumes it; nothing is torn.
* **Kill switch semantics.** The mode is asked again before every batch. The process environment of a running process cannot change, so the probe re-reads the settings (the `.env` file is re-read, environment variables are not) and **any unreadable value means `off`**; to change a mode set through the environment, stop the process (SIGTERM ends at the next batch) and start it with the new mode. Setting `off` or `dry_run` in the file, or stopping the process, therefore halts deletion within one batch, not one tick.
* **Locks.** One tick at a time (the existing advisory lock; a second tick returns `locked_out` and does nothing), plus an advisory lock per workspace (`abb_retention:<workspace id>`) so a future purge command and a tick never work on one workspace together; a held workspace is skipped (`skipped_locked`), not waited for.
* **Failure of one run never blocks others.** A step that raises records a fixed `failure_code` (`artifact_store`, `database`, `timeout`, `internal`; never an error text, INV-5) and increments `attempts`; the run is retried on the next tick until `ABB_RETENTION_MAX_ATTEMPTS` (default 5), then left for an operator: `python -m abb_api.retention retry-failed` gives them a fresh set. `TickResult.failed` and the log show the count.
* **A run that is no longer expired** when it is about to be marked (it was updated after selection) is left alone; one whose progress row exists is finished whatever the policy says now (half deleted detail is not worth keeping).
* **The artifact directory must exist (chosen).** Deleting a file that is not there succeeds, so a process pointed at the wrong directory would delete every artifact row and leave the files. `enforce` refuses to start (exit 2) unless `ABB_ARTIFACT_DIR` is an existing directory; compose mounts the API's volume into the retention service. A stored key that is not a valid key of the store has no file to remove and its row goes.

### 4. Reports, audit rows and tombstones

* `retention_reports` rows get `mode = enforce` and the counts actually deleted: for the tier, runs completed, events, artifact bytes, other rows, `truncated` when a bound or the kill switch ended the pass early. A dry run beforehand reports the same figures for the same data (a test compares them).
* **Audit rows.** Every enforced **pass** (one workspace, one tier, one tick) that deleted or failed anything writes ONE `audit_log` row: actor `system:retention`, resource `workspace`, a fixed action per tier (`retention.delete.artifacts|trace|run|audit|operational`), details with counts only (tier, mode, runs, events, rows, artifact_bytes, failed, truncated). **(chosen)** For the run tiers a "batch" in the audit sense is the pass, not each row batch (a million-event run would otherwise write hundreds of rows); a crash loses that pass's audit row, and the tombstones, which commit with the deletes, are the authoritative record (KI-200). The **audit tier** is stricter: each delete batch writes its summary row (rows, oldest and newest age) in the **same transaction**, so no audit entry is removed without a record of the removal.
* **Audit-log exemption.** The purge deletes only `occurred_at < cutoff` of the workspace, so the summary rows, written now, last until their own age like any entry; the retention role holds `DELETE` on `audit_log` (ADR-159) and the application role still cannot. Install-level clean-ups (`platform`) have no workspace and so no audit row (the column is NOT NULL); their `retention_reports` row is the record.

### 5. The fixed windows (not configurable)

| Table | Rows removed | Window |
| --- | --- | --- |
| `outbox_jobs` | status `done`; status `dead_letter` | 7 days; 30 days after `updated_at` |
| `rate_limit_counters` | expired counters (a workspace's, and the install's global ones) | 1 hour after `expires_at` |
| `project_usage_daily` | usage rows | 35 days |
| `analytics_dirty` | settled markers (`version = refreshed_version`) | 35 days |
| `sessions` | expired, idle-expired or revoked | 7 days after |
| `users` | people who never joined a workspace: no membership, no open invitation, no live session, nothing referencing them | 30 days after first sign-in |
| `login_states` | (the application deletes them when consumed and when it starts the next login) | none needed |

**Usage rows stay at 35 days, not "the run-record tier" (chosen):** the step-1 dry runs, the documentation and the reports already say 35, and the quota check reads today's row only. Run-bound jobs (`summarize_run`, `detect_findings`, `evaluate_run`, keyed `<workspace>:<run>`) go with the trace tier. The people clean-up re-evaluates the full predicate inside its own `DELETE`, one person per savepoint, so a reference that appears after selection (KI-194) keeps the person, and a foreign key from something the predicate does not know keeps them too.

### 6. Artifacts, the quota counter and orphans (KI-040)

Artifact rows are deleted in the same transaction that decrements `project_artifact_usage.bytes_stored` by exactly the bytes of the rows that transaction deleted (`GREATEST(.., 0)`; the counter only counts uploads made while quotas were on). **Orphan rule (chosen):** an artifact whose run does not exist is deleted once its own `created_at` is older than the artifacts tier (the server's time, not the client's), file first; no tombstone (there is no run to name), counted in the pass's audit row. A run that never arrives therefore cannot keep files for ever. Files with no row at all (a lost duplicate upload, a crash between write and insert) are not found by this job (KI-196).

### 7. The dirty marker (KI-057)

A change that committed while a refresh ran and coalesced into its already-claimed job was lost. `analytics_dirty (workspace_id, day, version, refreshed_version)`: the summarizer bumps `version` in the transaction that changes the run (so the bump is visible exactly when the change is); the refresh job reads the version (a plain read, never an upsert that would wait for an open writer; only a missing row is created), rebuilds the day, then compare-and-sets `refreshed_version = seen WHERE version = seen`. If the CAS fails a writer committed meanwhile and the job queues another refresh of the day (coalesced, bounded: it clears when the next CAS succeeds). A writer still in flight holds the row lock, so the CAS waits and then compares against the committed version. Each interleaving is a deterministic test (`tests/test_analytics_dirty.py`). The retention job does not use the marker: it rebuilds the day itself, directly, after its own deletes.

### 8. Grants (migration 0102, explicit lists, pinned by `tests/retention/test_retention_role.py`)

On top of ADR-159: `UPDATE (trace_state)` on `runs` and `UPDATE (bytes_stored)` on `project_artifact_usage` (columns, never the tables); `SELECT, INSERT, UPDATE, DELETE` on `retention_run_progress`; `SELECT, INSERT, DELETE` on the five `analytics_*_daily`/`top_runs` tables (the statements `refresh_day` executes); `SELECT, DELETE` on `analytics_dirty`. The application role loses `INSERT/UPDATE/DELETE` on the progress table (it may read it); it keeps what it had on `analytics_dirty`. Still nothing on `api_keys`, secrets, alert tables, policies, `login_states`; the application still cannot `UPDATE` or `DELETE` events or delete runs. The API process holds no retention credentials (unchanged, asserted).

### 9. The table inventory (fail-closed)

`abb_api/retention/inventory.py` classifies every table that names a run (a `run_id`-like column, or a foreign key to `runs` or `events`), and `tests/retention/test_inventory.py` fails for one that is not classified. The deletion steps are built from the same lists.

| Table | Class | How |
| --- | --- | --- |
| `events`, `spans`, `cost_calculations`, `findings`, `run_findings`, `evaluations` | retained-detail | trace tier, batched, in that order |
| `run_search` | retained-detail | trace tier, the search step |
| `artifacts` | retained-detail | artifacts tier: files, rows, counter |
| `outbox_jobs` | retained-detail | trace tier: jobs of the three run-bound types keyed to the run |
| `runs` | retained-record | run tier, last; `trace_state` marks the stages before |
| `experiment_runs` | retained-record | cascades with the run record |
| `analytics_top_runs` | retained-record | rebuilt for the day after the run goes |
| `approvals` | exempt | governance record with no foreign key to the run; its scrubbed arguments outlive the trace tier (KI-195) |
| `alert_notifications` | exempt | pruned by its own delivery-log window |
| `retention_tombstones`, `retention_run_progress` | exempt | the job's own record and working state |

## Threat model additions

* **A wrong policy** (an owner shortens a tier, or a compromised API shortens it): bounded by the floor (7 days), the order rule, the per-tick caps and the batch order; the damage is earlier deletion of that workspace's data, in `enforce` mode only, which an operator sets. The runbook requires a backup first.
* **A wrong deployment** (artifact directory, database): the directory must exist; the job refuses to start with a role more privileged than `abb_retention` in production (ADR-159).
* **A hostile run** cannot delay its expiry beyond the clamp, nor make a tick larger than the caps; its late events can re-create a deleted run (KI-197).
* **Compromised retention credentials** gain, over ADR-159: update of `runs.trace_state` and of the artifact byte counter, insert/delete in the rollups (derived data) and the progress table. They already could delete the retained data.

## Alternatives considered

* *An outbox job type per run*: the worker would need `DELETE` on events. Rejected (ADR-159).
* *Mark `deleting` at selection*: a store outage would label a whole, readable trace as going. Rejected for the mark-after-artifacts order.
* *Refresh the rollup before deleting the record*: still counts the run. Rejected.
* *A mode stored in the database as the kill switch*: another privileged write path. Rejected for now; the env/file re-read plus SIGTERM, every batch, is enough and needs nothing new.
* *One audit row per row batch for every tier*: unbounded noise for a large run. Kept only for the audit tier.
* *An index on the run-end expression (KI-193)*: every summary rewrite updates `updated_at`, so the index would defeat HOT updates on the hottest table. Not added; the caps and timeouts bound the scan. To measure and revisit.

## Consequences

* New process requirement: `ABB_ARTIFACT_DIR` mounted in the retention process; enforcement exits 2 otherwise.
* A migration with a new `runs` column (constant default, a catalog change) and two small tables.
* Operators get `retry-failed` and the progress table as the place to look: `SELECT tier, attempts, failure_code, count(*) FROM retention_run_progress GROUP BY 1,2,3`.
* New known issues: KI-195 (approvals keep scrubbed arguments past the trace tier), KI-196 (artifact files without a row are never swept), KI-197 (late events for an expired or deleted run), KI-198 (while a run is `deleting` the events and spans endpoints return partial data; `trace_state` is the signal, the UI must read it), KI-199 (rollup split after the trace tier), KI-200 (a crash loses a pass's audit row; tombstones are authoritative).
