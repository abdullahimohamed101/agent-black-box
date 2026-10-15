# ADR-159: Data retention: a separate role and process, dry run first

Status: Accepted (Phase 19b, step 1: the foundation; enforcement follows in step 2). Amends INV-1 (AGENTS.md, spec §62). Deviates from spec §94 in one stated way. Resolves, over steps 1-2, KI-021, KI-040, KI-055, KI-072, KI-103, KI-180, KI-184.
Date: 2026-10-15
Phase: 19b

## Context

Nothing in the product ever deletes data. The application's database role cannot delete events by design (INV-1, KI-020, migration 0007), so retention cannot be an application feature: it needs a privilege the API must never hold, a policy people can set, and a rollout that cannot destroy data by surprise. Decisions D1-D3 of the Phase 19b plan were settled with the user.

## Decision

### 1. Mechanism (D1)

* A database role **`abb_retention`** (migration 0101) is the only role with `DELETE` on the retained tables, including `events`. A scheduled **retention job**, a process of its own (`python -m abb_api.retention run` or `python -m abb_api.cli retention run-once`), deletes expired data by whole run, in batches (step 2). The API and the normal worker never delete events, never start the job and never hold its credentials.
* **INV-1 is amended**: *accepted events are immutable until expired by the retention job (ADR-159)*. Nothing else may update or delete them; corrections remain new events or derived records.
* The role has **no default privileges**: every grant is explicit, so a table added later is invisible to it until its own migration says otherwise. It holds `SELECT` on what it must read, `DELETE` on the retained tables (events, spans, cost lines, findings, run findings, search documents, evaluations, artifact rows, runs, outbox jobs, audit log, rate-limit counters, daily usage, sessions, users, its own reports), `INSERT` on `audit_log` (one row per batch, step 2), `retention_reports` and `retention_tombstones`. Sessions and users are readable **by column** (never the token hash); `approvals` by the two user-id columns only. It holds **nothing** on `api_keys`, `server_secrets`, the alert tables, policies, `workspaces`/`projects` beyond `SELECT`, or `alembic_version`, and no `UPDATE` or `TRUNCATE` anywhere. Cascades (spans, cost lines and so on, when a run goes) run as the table owner and need no grant. Step 2 may add a grant in its own migration (for example `UPDATE` on `project_artifact_usage` to decrement stored bytes); `tests/retention/test_retention_role.py` pins the exact set, so any change is a deliberate edit.
* The application role gains `SELECT` only on `retention_reports` and `retention_tombstones` (it can neither forge nor erase what the job reports), and `SELECT, INSERT, UPDATE` on `retention_policies` (never `DELETE`). The runtime role still cannot `UPDATE` or `DELETE` events.
* The job's connection URL is its own setting, **`ABB_RETENTION_DATABASE_URL`**, read only by `abb_api.retention.runner` (a settings class separate from the API's `Settings`). There is no fallback to the runtime role's URL. In development and test only, it may fall back to the owner URL the migrations use (`ABB_MIGRATION_DATABASE_URL`, then `ABB_DATABASE_URL`), with a warning. Staging and production refuse to start without the explicit URL. At start the job also asks which role it is: a superuser, `BYPASSRLS`, an owner of any table, `CREATE` on the schema or any access to the credential tables is "too privileged", refused in production (closed reasons, like ADR-155), warned elsewhere.

### 2. Policy (D2)

Per workspace, four tiers, each in whole days after the run **ended** (the audit log: after the entry was written), with a **floor of 7 days** (a typo must not delete everything on the next tick) and a ceiling of 36,500, or the explicit choice **forever**:

| Tier | What it covers | Default |
| --- | --- | --- |
| `artifacts` | the files and their records | 30 days |
| `trace` | events, spans, cost lines, findings, evaluations, search entries, run-bound background jobs | 90 days |
| `run` | the run record and its rollup contribution (the run page then says "trace expired") | 365 days |
| `audit` | the audit log (KI-072), independent of the other three | 365 days |

Defaults come from `ABB_RETENTION_{ARTIFACTS,TRACE,RUN,AUDIT}_DAYS` (a number or `forever`), validated at startup. **Ordering rule (chosen here):** `artifacts <= trace <= run`, with `forever` counting as infinity. The run record is deleted last (events reference it with RESTRICT), so a run record kept for less than its trace detail would be a contradiction; a policy that breaks the rule is refused when saved (`422 RETENTION_POLICY_INVALID`, closed reasons `below_floor`, `above_ceiling`, `order`, never repeating the value), at startup for the environment defaults, and skipped (logged, counted) by the job if a later change of the defaults made a saved policy invalid.

Fixed windows (not workspace settings) for operational tables: finished background jobs 7 days, dead letters 30 days, expired rate-limit counters 1 hour, daily usage rows 35 days, dead sessions 7 days after they expired or were revoked, and people who never joined a workspace and have no open invitation or reference 30 days after sign-in (KI-184).

**A run's end** is `completed_at`, or its last update while it has none, **clamped** between the server's first sight of the run (`created_at`) and its last update (`updated_at`). Client-claimed times are hostile input: an end claimed in the distant past must not expire a run on the spot, nor one in the future keep it for ever. An artifact whose run does not exist ages by its own upload time (KI-040).

The policy is read by every member and changed by owners and admins (`retention.read`, `retention.write`; no API key holds either), audited per changed tier (`retention.policy.update`: tier, old and new value only), and rate limited per workspace (closed scopes `retention_write`, `retention_preview`). Routes: `GET`/`PUT /v1/workspaces/{workspace_id}/retention`, `GET .../retention/preview` (current or proposed policy, bounded by a statement timeout and per-tier caps, `504` on timeout), `GET .../retention/reports`. A workspace id that is not the caller's is a `404`, like an unknown one.

### 3. Rollout safety (D3)

`ABB_RETENTION_MODE` is `off`, `dry_run` (**the default**) or `enforce`. In dry run the job executes the real selection and records, per workspace and tier, what it **would** delete (runs, events, artifact bytes, other rows, whether a bound was reached, the cutoff): counts only, never content, in `retention_reports`. The default is dry run so that an upgrade, or a typo in a mode, can never remove data; an operator reads the reports and the preview, takes a backup and a restore drill (step 4), and only then sets `enforce`. **In this step `enforce` is a valid, validated value that the job refuses to act on** (it logs the refusal once, exits 2 from `run-once`, writes nothing and deletes nothing); the code that deletes arrives in step 2. A tick is bounded: a time budget (checked before each workspace; the workspace reported on longest ago goes first, so a tick that runs out of time resumes with the rest), at most `ABB_RETENTION_MAX_RUNS_PER_TICK` runs and `ABB_RETENTION_MAX_ROWS_PER_TICK` rows per tier and workspace, a statement timeout on every query, and one tick at a time (an advisory lock). Reports are kept at 50 per workspace and tier. `retention_tombstones` (run id, workspace, tier, mode, time) exists now and is written from step 2.

### 4. Planned order of deletion (step 2, recorded here so the grants make sense)

Artifact files, then search entries, then events and derived rows (events first, then the rows that reference the run), then the run record last; a run is marked "deleting" first so readers never see a broken page; completion is recorded per backend and partial failures are retried; one audit row per batch with counts only; the analytics rollups of affected days are refreshed.

### 5. Deviation from spec §94

§94 lists prompt/response at 30 days and structured events at 90. Prompt and response bodies live **inside** events, which are immutable to every role but the retention job, and scrubbing a field in place would need `UPDATE` on `events`. They therefore follow the **trace** tier (90 days by default). A deployment that needs content gone sooner has a documented workaround today (capture mode `METADATA_ONLY` or `DISABLED`, ADR-130) and a recorded known issue for in-place scrubbing (KI-192).

## Threat model

* **A compromised API or worker process** holds `abb_runtime`: it can append events and write policies (audited) but cannot delete or update events, delete runs, projects or workspaces, nor write or erase reports and tombstones. It can *shorten a policy*, which at worst makes the job delete data of that workspace earlier, after the floor, in the order and at the pace of the job, and only in `enforce` mode, which an operator sets. It cannot trigger a tick (no route, no import: asserted by a test that imports the application in a clean process) and does not hold the retention URL (absent from the API's settings class, asserted too).
* **Compromised retention credentials** can delete only the retained tables, read only what is granted (ages and ids of sessions and users, never a token hash, never an API key, secret or alert destination), insert audit rows and reports, and cannot alter the schema or any role. They can erase history of the retained kinds, including the audit log: protect them like the owner's and keep the process off the public network.
* **The owner and superusers** can still rewrite history (unchanged since migration 0007).
* **Hostile telemetry** cannot move its own expiry beyond the clamp above, and cannot enlarge a tick: caps and timeouts bound it.

## Alternatives considered

* *Do it in the API with a temporary privilege (`SET ROLE`)*: leaves the privilege reachable from the process that handles untrusted input.
* *An owner-run SQL job*: no audit, no dry run, no bounds, and the owner's credentials would live in cron.
* *A job type in the normal worker*: the worker runs as `abb_runtime`; it would need the privilege or a second connection with the retention credentials inside a process that also runs alert delivery to the internet.
* *Per-project policies*: more surface for a first release; workspace level covers the stated need.

## Consequences

* Two more roles to provision (`ABB_RETENTION_DB_PASSWORD` for `alembic upgrade`, like the runtime role) and one more process (`retention`), documented in `docs/OPERATIONS.md`.
* The run-end expression has no index; a very large `runs` table will want one before enforcement (recorded for step 2).
* Existing API tests are unchanged except where the exact table privileges are pinned (`tests/test_runtime_role.py` now lists the two read-only report tables and the policy table without `DELETE`).
