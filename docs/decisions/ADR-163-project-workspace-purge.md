# ADR-163: Project and workspace purge: owner-run, dry run by default, hidden first, resumable, recorded without content

Status: Accepted (Phase 19b, step 5). Implements decision D5 of the Phase 19b plan; resolves KI-170. Builds on ADR-159 (the role) and ADR-160 (the per-run procedure, which this reuses and does not duplicate).
Date: 2026-10-16
Phase: 19b

## Context

Retention (ADR-160) removes data by age. Nothing removes a project or a workspace on request: the schema forbids it (the runtime role cannot delete projects or workspaces, `events` restricts the run delete, alert rules restrict the project delete, KI-170), so an owner who must erase a customer's data has had no supported way. Deleting a tenant's data is the most destructive thing the product can do, so the design starts from what could go wrong: deleting the wrong scope, deleting half and leaving a half-visible tenant, leaving data behind in a backend the SQL does not reach (artifact files, search entries, rollups), and giving the API process a way to do it.

Settled with the user (D5): owner-run CLI commands, not API endpoints, not reachable from the API process; dry run by default; `--confirm` with the exact slug; audited; cascade through rules, usage rows and artifacts; completion per backend; resolves KI-170. Everything below beyond that is **(chosen)**.

## Decision

### 1. The commands

```
python -m abb_api.retention purge-project   --workspace <slug> --project <slug> [--dry-run] [--confirm <project-slug>]
python -m abb_api.retention purge-workspace --workspace <slug>                  [--dry-run] [--confirm <workspace-slug>]
```

(also `python -m abb_api.cli retention purge-...`). They run in the retention process's environment with the retention role's credentials (`ABB_RETENTION_DATABASE_URL`, the same production refusal of an over-privileged role as `enforce`), and need the artifact directory like enforcement. `ABB_RETENTION_MODE` does not matter: a purge is an explicit operator action, not a scheduled one.

* **Default is a dry run** (no `--confirm`, or `--dry-run` even with `--confirm`): it prints rows per table, files and bytes in the artifact store, search entries, rollup rows and, for a workspace, how many people are tied to it and how many the user rule could remove, and deletes nothing (a test compares every table before and after).
* **Deletion needs `--confirm <slug>` equal to the project slug (project purge) or the workspace slug (workspace purge).** A mismatch refuses before anything is read, locked or marked (exit 2). A slug that matches nothing, or names a project of another workspace, is the one answer `not_found` (exit 3).
* **Exit codes:** 0 done or dry run; 2 wrong confirmation or unusable configuration; 3 refused (`not_found`, or `locked`: another purge or a retention tick holds the workspace); 4 a step failed (a fixed code is printed: `artifact_store`, `database`, `timeout`, `internal`; never an error text); 5 stopped by SIGTERM/SIGINT at a batch boundary. After 4 and 5 the same command resumes.
* Output carries counts, the slugs the operator typed and fixed codes. Nothing taken from the data is printed or logged.

### 2. `purging`: readers stop first, nothing is half visible

`projects.purge_state` and `workspaces.purge_state` (`active` | `purging`, CHECK, partial index; migration 0104). The first transaction of a purge inserts the `purge_tombstones` row and sets `purging`: there is never a purging scope without a record. From then on:

* **Keys stop working at once.** `ApiKeyLookup.find` computes whether the key's project or workspace is purging and `is_active` is false for it; authentication answers the uniform `401 API_KEY_INVALID` (unknown, revoked, expired and purging are one answer, so ingestion for a purging project is "refused with a fixed code" without telling a key holder anything the code did not already). Open streams end the same way (`credential_still_grants`).
* **The project reads as not found.** `ProjectRepository` omits it (so `authorise_project` answers `404 PROJECT_NOT_FOUND`, and `GET /v1/projects` does not list it) and `RunQueries` omits its runs (`404` for a run, absent from lists and facets).
* **The workspace is a 404 for its members.** `MembershipRepository.role_of` returns "not a member" for a purging workspace, which is what `_user_principal` already turns into `404 WORKSPACE_NOT_FOUND`; `/v1/me` stops listing it.
* The mark is the only thing the API process ever sees of a purge. Reads of a purging project through the other per-run endpoints (events, spans, findings, analytics, comparison) are not filtered (KI-211): they show what is left of an authorised caller's own data until its run is deleted, and the project, its keys and the run list are gone from the first moment.

The retention role holds `UPDATE (purge_state)` on those two tables (columns, not tables).

### 3. The procedure: the engine's per-run deletion, then the rows, the root last

The whole-run procedure of ADR-160 is reused unchanged: `Purger` subclasses `Enforcer` and creates a `run`-tier row in `retention_run_progress` for every run of the project (no expiry cutoff applies), then calls the same `_steps`: artifact files through the `ArtifactStore` contract and then rows and the quota counter, the run marked `deleting`, the search entry, events/spans/derived rows in batches, the run record and its `retention_tombstones` row (tier `run`), per-backend flags in the progress row, the rollups of every affected day rebuilt by `refresh_day`. Order of a purge:

1. **mark** (above), then **measure**: the counts that go in the record, taken after the mark so they are stable.
2. **runs**: every run of the project, a batch at a time.
3. **artifacts without a run** (a project's orphans, KI-040) through the same contract.
4. **analytics**: rebuild; a day that cannot be rebuilt fails the purge (fixed code) rather than leaving the project's share in a rollup.
5. **rows**, in foreign-key safe order and bounded batches: alert notifications, alert rules (KI-170; their destination links cascade), approvals, evaluators, experiments (variants and links cascade), policies and versions, pricing overrides, agent versions and agents, **API keys**, the five rollup tables (rows of days with no run left), usage rows, the project's counters, the engine's own progress rows. Tables the role may read only by key columns (credential and content tables) are batched by their key (`DELETE ... USING (SELECT keys ... LIMIT n)`); the others by `ctid` like the retention clean-ups.
6. **the root**: in ONE transaction, a leftover check over every table of the inventory (any row of the scope left refuses the root's deletion with `internal`: fail closed), the audit row, `DELETE FROM projects` (which cascades the project's security settings, quota overrides and artifact counter) and the tombstone's completion.

A **workspace purge** does steps 1 to 6 for every project (each project row goes as soon as its data does), then the workspace-level rows in order (alert destinations with their sealed configuration, remaining jobs and counters, invitations, the audit log, memberships), then the people (section 5), then in ONE transaction a last sweep of audit rows that raced the mark, the leftover check and `DELETE FROM workspaces` (the retention policy, reports, run tombstones, progress and dirty markers cascade) with the completion.

Destinations are workspace-level: a project purge removes the project's rules and their links and leaves the destinations. `server_secrets` (the platform sealing keys) always stays: other workspaces' destinations are sealed with them.

### 4. Resumable, idempotent, one at a time

* **Ledger.** `purge_tombstones` is both the permanent record and the resume state: `steps_done` (coarse steps, appended after each), `counts`, `attempts` and `failure_code` (a closed set), `user_candidates`. A unique partial index allows one `running` row per project and one per workspace (the database is the second line after the lock). Running the same command again finds the `running` row and continues; it never creates a second. Every step is idempotent, so a crash at any point (tested after every hook call, for both scopes) ends in the state an uninterrupted purge reaches, with no leftover row and no duplicate tombstone.
* **Locks.** The purge takes the workspace's advisory lock `abb_retention:<workspace id>` (the one a retention tick holds while it works on that workspace) without waiting: a second purge, a purge of another project of the same workspace, or a tick on it is refused (`locked`, exit 3) with nothing changed.
* **Kill switch.** SIGTERM/SIGINT sets the `Control` stop event: the purge ends at the next batch boundary (exit 5), no half transaction. It does not consult `ABB_RETENTION_MODE`.
* **Bounds.** Batch size and statement timeout are the retention settings; the per-tick row and time budgets do not apply (a purge is run to completion by an operator).

### 5. The users rule (workspace purge)

Users are platform-level. A workspace purge removes the workspace's memberships and invitations and **deletes a person only if, after that, they have no membership anywhere, no live session, no open invitation addressed to them elsewhere, and nothing that references them** (the KI-184 predicate with its age condition removed, re-evaluated inside the `DELETE`, one person per savepoint: a reference the predicate does not know keeps the person). Their expired and revoked sessions go with them; a live session keeps the person (they may belong to a workspace the operator has not looked at, and signing them out is not the purge's job). The candidates are the workspace's members and the people who invited or accepted there, captured before the memberships go and stored in the record (ids) until completion, so a resumed purge knows them.

### 6. The record, and why the workspace's audit log goes with it

`purge_tombstones` (platform level, no foreign keys, so it outlives the scope): id, scope, workspace id, project id, a SHA-256 of the typed slug(s), tool version, state, requested and completed times, the steps done, `counts` (rows per table, files and bytes, search entries, rollup rows, people candidates and removed, measured when the purge began) and the failure code and attempt count. No names, no content. The runtime role has no privilege on it; the retention role can read, insert and update but not delete it.

* **Project purge**: one counts-only `audit_log` row in the workspace (`system`/`retention`, `retention.purge.project`, details: purge id, row count, artifact bytes), in the same transaction as the project row's deletion and the completion.
* **Workspace purge**: the workspace's own audit log is deleted with the workspace, as intended (it is the workspace's data, and keeping it would keep what an erasure request asks to remove). The counts that an audit row would have held, including the number of audit rows, are in the tombstone, which is the record. This replaces the "one audit row before deleting" of the plan text, which had nowhere to live.
* The per-run `retention_tombstones` of a purged project stay (ids only: they say that a run was removed); a workspace purge removes them with the workspace row, the purge tombstone remaining.
* **Counts are those at the start**, not a running total: a crash would make a running total wrong, whereas the start figures are exact and the leftover check proves nothing remained.

### 7. The table inventory (fail-closed)

`abb_api/retention/purge_inventory.py` classifies **every table of the schema** as purged-with-project, purged-with-workspace, kept-with-reason or exempt, with the predicates that select a project's or a workspace's rows. `tests/retention/test_purge_inventory.py` fails for a table that is not classified, for a tenant table classified as exempt, and for a purged table that has no way out (run procedure, drain or cascade); the deletion lists are the same data. The verification helper (`tests/retention/purge_helpers.py::assert_scope_gone`) does not use the inventory for its main claim: it scans every table that has a `project_id` / `workspace_id` column in `information_schema`, requires zero rows of the scope, checks the tables reached only through others with the inventory's predicates, checks that no file of the scope is left under the artifact directory, and that every other row of every table, and every other file, is byte-identical (a row hash per table excluding the timestamp columns that a rollup rebuild rewrites).

### 8. Grants (migration 0104, explicit lists, pinned by `tests/retention/test_retention_role.py`)

* `SELECT, INSERT, UPDATE` on `purge_tombstones`; `UPDATE (purge_state)` on `projects` and `workspaces`.
* `DELETE` on `projects`, `workspaces`, `agents`, `agent_versions`, `api_keys`, `approvals`, `evaluators`, `experiments`, `policies`, `policy_versions`, `pricing_overrides`, `alert_rules`, `alert_destinations`, `alert_notifications`, `invitations`, `workspace_members`.
* For the credential and content tables, **column-level `SELECT` of the key columns only** (`workspace_id`, `project_id`, `id`, and the parent column for agent versions, experiment links and rule links): a `WHERE` clause can be evaluated, a secret cannot be read. `SELECT *` or `SELECT secret_hash` on `api_keys`, `config_encrypted` on `alert_destinations`, policy documents and alert payloads stay `permission denied` (tested).
* The retention process's startup check ("credential tables") now refuses a role that can read or forge a credential (any `SELECT`/`UPDATE` on `secret_hash`, `config_encrypted`, `signing_secret_encrypted`, any `INSERT` on `api_keys` or `alert_destinations`, any access to `server_secrets`); `DELETE` alone is the purge's privilege and is no longer a refusal.
* The runtime role loses everything on `purge_tombstones` and still cannot delete projects, workspaces, runs or events (tested). The API process never imports `retention.purge*` or the runner (tested), and the credentials string stays in `runner.py` alone.

## Threat model

* **A compromised API process cannot purge**: no credentials for the retention role, no DELETE on the roots or the run data, no access to the record, and the code is not loaded in its process. It can (as before) write the application tables it owns; it can set `purge_state` (table-level `UPDATE` on `projects` and `workspaces`, KI-212), which hides a project or workspace but deletes nothing and is undone by setting it back.
* **Compromised retention credentials** (operator level) already could delete retained data. They gain, over ADR-160: delete the rows of credentials and configuration tables (keys, destinations, policies, rules, members, invitations), delete projects and workspaces (the foreign keys refuse a root with children, so the data goes first), and update `purge_state`. They cannot read a key secret, a destination's sealed configuration, or policy content. KI-213 records the option of a separate `abb_purge` role so the always-on retention tick does not carry these.
* **The wrong target**: the exact slug is typed, a project slug cannot confirm a workspace, a slug of another workspace's project is `not_found`, the default is a dry run, and the runbook requires a backup first. Permanent means permanent: backups taken before the purge still hold the data until they expire (KI-215).
* **A hostile tenant** cannot start a purge or change what it deletes; names and content never reach the record, the output or the logs.
* **A race with ingestion** that began before the mark may leave a run or artifact behind; the purge sweeps both again before the root and refuses the root if anything is left.

## Alternatives considered

* *API endpoints for owners*: the approved decision is a CLI. An endpoint would put the most destructive action behind the session a phished owner holds, and would need the retention privileges in the API process. Rejected.
* *`ON DELETE CASCADE` on projects and workspaces*: one statement, but it leaves artifact files and search entries, deletes millions of rows in one transaction, skips the progress records and gives the runtime role a way to erase history if the grant ever widened. Rejected; the foreign keys stay restrictive and are the safety net (a root with children cannot be deleted).
* *Soft delete (`deleted_at`) and a later sweep*: leaves the data until the sweep runs and makes every reader responsible for filtering. The `purging` state is a short-lived fence, not the deletion.
* *A separate `abb_purge` role*: cleaner privilege boundaries, but a second credential to provision and rotate for a command run a few times a year. Deferred (KI-213).
* *Filtering every read path for `purging`*: dozens of repositories. The choke points (keys, projects, runs list/get, membership) carry the fence; the rest is KI-211.
* *One audit row for a workspace purge before the audit rows go*: it would be deleted with them. The tombstone is the record.

## Consequences

* Migration 0104 (reversible; its downgrade discards the purge record and would un-hide a purging scope: finish or restore, do not roll back mid-purge). New process requirement: none beyond enforcement's (the artifact directory).
* New tests: `tests/retention/test_purge*.py` (destructive path against real Postgres and a real artifact directory, crash injection after every step for both scopes, concurrency by a held advisory lock, SIGTERM semantics by the stop event, failure codes, the users rule, the API view of a purging scope, the CLI), the privilege pins, the inventory.
* Modified existing tests: `tests/retention/test_retention_role.py` (the exact grant set now includes the purge grants; the "nothing on credentials" test says "only DELETE and key columns"; two statements that are now permitted were removed from the denied list: `DELETE FROM api_keys|workspaces|projects`, and denied statements for secrets, content, `purge_state` and the purge record were added). No test was weakened or skipped.
* New known issues: KI-211 (other per-run reads of a purging project), KI-212 (runtime role can write `purge_state`), KI-213 (a separate purge role), KI-214 (non-run background jobs of a purged project), KI-215 (backups keep purged data), KI-216 (artifact files without a row, KI-196, survive a purge).
