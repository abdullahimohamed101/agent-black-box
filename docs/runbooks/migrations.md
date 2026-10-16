# Migrations: upgrading safely and rolling back

Every migration is classified in `apps/api/migrations/safety.json` (checked by `tests/test_migration_safety.py`, which fails for an unclassified or stale entry and holds every `reversible` downgrade to the schema and privileges of the previous revision). Two classes:

- **reversible**: `alembic downgrade <previous>` restores the previous revision exactly. It removes only objects the migration created (what is discarded is listed per migration: **those objects' contents are lost**), deletes or rewrites no earlier row, cannot fail on data and weakens no protection.
- **forward-only**: rolling back is **restore the backup you took before the upgrade**. A downgrade may exist and may run, but it deletes or rewrites rows, can fail on data, or drops a record that cannot be recreated.

A downgrade is for undoing the upgrade you just ran, one or two steps, not for travelling back in time: walking back through 0002 empties the installation.

## The safe upgrade

1. **Back up**: `make backup OUT=... BACKUP_FLAGS="--encrypt ..."` (runbook `backup-restore.md`) and `verify` it. Retention should not be enforcing during it.
2. Read the entries of the migrations you are about to apply (below, or `safety.json`: `class`, `upgrade_risk`, `downgrade_discards`). Schedule lock-heavy ones for a quiet period.
3. `alembic upgrade head` as the owner role (`make migrate`, or the compose `migrate` job). The upgrade is transactional per revision.
4. Verify: `alembic current` is the new head; `curl /readyz`; open a run; the worker drains (`outbox_jobs`); for 0041/0042-style changes run the rebuild the migration names (`refresh-analytics`, `rebuild-findings`, `search-rebuild`).
5. Keep the backup until the new version has run through a normal cycle.

## Rolling back

- All migrations since the backup are `reversible`: `alembic downgrade <revision before the first one you applied>` (as the owner), then deploy the previous application version. Anything the discarded objects held (see the table) is gone.
- Any `forward-only` migration among them, or any doubt: **stop the processes, restore the pre-upgrade backup** into a new database (`backup-restore.md`), point the application at it, deploy the previous version. Data written since the backup is lost; that is the cost of a forward-only change.

## Adding a migration

Classify it **in the same commit**: add `"<id>": {"class": ..., "notes": ...}` to `safety.json` (read its `_how_to_classify`). Write `downgrade()` so a `reversible` claim is true; the chain test will say what it failed to restore. If the downgrade deletes or rewrites rows, can fail on data, or depends on a job having run, the class is `forward-only` and the note must say that rollback is a restore. Record lock-heavy steps in `upgrade_risk`.

## Per-migration notes

`reversible` unless marked **forward-only**. "Discards" is what a downgrade of that revision throws away.

| Revision | Class | Note |
| --- | --- | --- |
| 0001 | reversible | Empty baseline; the downgrade is a no-op. |
| 0002 | reversible | Creates the tenancy tables. Rolling back past it empties the installation. Discards: workspaces; users; workspace_members; projects; agents; agent_versions; api_keys. |
| 0003 | reversible | Creates runs, spans and the append-only events table. Discards: runs; spans; events. |
| 0004 | reversible | Creates the outbox. Discards: outbox_jobs. |
| 0005 | reversible | Skeleton tables (artifacts, evaluations, policies) that later revisions extend. Discards: artifacts; evaluations; policies. |
| 0006 | reversible | Index on outbox_jobs. **Upgrade:** A plain CREATE INDEX holds a write-blocking lock on outbox_jobs for the build; time grows with the table. Run in a quiet period on a large database. |
| 0007 | reversible | Creates the abb_runtime role (never dropped: roles are cluster-wide) and its grants; the downgrade removes this database's grants, so the application cannot work until 0007 is applied again. Security-relevant: do not leave a deployment between revisions. |
| 0008 | reversible | Makes events.run_id RESTRICT and removes the runtime role's DELETE on the parents of events. The downgrade restores CASCADE and re-grants DELETE, which reopens a path that deletes events (INV-1). Mechanically reversible; do not leave a production database there. |
| 0009 | reversible | Index on events (run, arrival). **Upgrade:** A plain CREATE INDEX on events blocks inserts for the build, which grows with the table; this is the largest table. Build in a quiet period, or create the index CONCURRENTLY by hand first (same name) and let the migration skip it. |
| 0040 | reversible | Cost lines (derived) and pricing overrides (operator-entered). Discards: cost_calculations; pricing_overrides (operator-entered prices are lost). |
| 0041 | reversible | Typed analytics columns on runs and spans; all derived (INV-2). Rebuild with refresh-analytics. Discards: runs typed figure columns; spans.project_id and run_started_at. **Upgrade:** The backfill UPDATE rewrites every run and every span row once, and the two indexes are built inside the transaction (write-blocking). Run off-peak on a large database (KI-056). |
| 0042 | reversible | Daily analytics rollups; derived and rebuildable. Discards: analytics_*_daily and analytics_top_runs. |
| 0043 | **forward-only** | Widens money columns to numeric(38,9). The downgrade narrows them back and fails (numeric field overflow) as soon as any stored value does not fit numeric(20,9); a rollback cannot be guaranteed. Rollback = restore the pre-upgrade backup. |
| 0044 | **forward-only** | Artifacts become project-scoped and lose the run foreign key. The downgrade DELETES artifact rows whose run never arrived (their files stay behind, orphaned) and drops name, media type and project columns. Rollback = restore the pre-upgrade backup. |
| 0045 | reversible | Identity columns on users, sessions and login states. Discards: sessions; login_states; users identity link (provider, subject, last login). |
| 0046 | reversible | Invitations and membership bookkeeping columns. Discards: invitations; workspace_members.invited_by and updated_at. |
| 0047 | reversible | Append-only audit log. Rolling back drops the whole audit history; acceptable only immediately after the upgrade that created it. Discards: audit_log (the audit history). |
| 0060 | reversible | Findings tables; derived (rebuild-findings). Discards: findings; run_findings. |
| 0070 | reversible | Evaluators and evaluation state columns plus a trigger that makes finished evaluations immutable. Discards: evaluators; evaluation results and state columns. |
| 0071 | reversible | Experiments, variants and attached runs. Discards: experiments; experiment_variants; experiment_runs. |
| 0080 | reversible | Per-run search document; derived (search-rebuild). Discards: run_search. |
| 0090 | reversible | Per-project security settings. Without a row a project behaves as METADATA_ONLY, so rolling back changes capture behaviour. Discards: project_security_settings (capture modes, mutes, allow-lists). |
| 0091 | **forward-only** | Widens finding severities and adds the security badge. The downgrade DELETES security findings and findings with the new severities (derived, but a table that existed before) and swaps CHECK constraints back. Rollback = restore the pre-upgrade backup, or rebuild-findings after a downgrade. **Upgrade:** The partial index is built inside the transaction and blocks findings writes for the build (KI-137). Small today; on a large database create it CONCURRENTLY first (0041 precedent). |
| 0092 | reversible | Append-only policy version history. Discards: policy_versions. |
| 0093 | **forward-only** | Approvals with a database-enforced state machine and a wider audit actor kind. The downgrade REWRITES audit rows (actor_kind system to cli) in the append-only audit log, drops all approvals and their trigger functions. Rollback = restore the pre-upgrade backup. |
| 0094 | **forward-only** | Server-only secrets. The downgrade drops the table that holds the event-id key: a new key would change the ids of future server-written events and break deduplication against stored ones, and the alert-encryption key (0095) could never be recovered. Never downgrade past 0094; rollback = restore the pre-upgrade backup. |
| 0095 | reversible | Alerting tables; destinations hold sealed secrets. Discards: alert_destinations; alert_rules; alert_rule_destinations; alert_notifications. |
| 0096 | **forward-only** | Two new skip reasons and an index. The downgrade REWRITES notification rows (rate_limited and daily_cap become destination_paused), which loses information. Rollback = restore the pre-upgrade backup. |
| 0097 | reversible | Shared limiter counters; operational, rebuilt by traffic. Discards: rate_limit_counters. |
| 0098 | reversible | Project quota tables. Artifact bytes are backfilled from artifacts at upgrade; daily usage counting starts at the upgrade. Discards: project_quota_overrides (operator quota overrides are lost); project_usage_daily; project_artifact_usage. **Upgrade:** The backfill scans the artifacts table once (an aggregate INSERT ... SELECT) inside the migration transaction; time grows with the number of artifacts. |
| 0099 | reversible | Session device hint column and an invitation lookup index. Discards: sessions.device_hint. |
| 0100 | reversible | Removes DELETE on the quota tables from the runtime role and adds a key index. The downgrade re-grants DELETE (a privilege widening); mechanically reversible. |
| 0101 | reversible | Creates the abb_retention role grants and the retention policy, report and tombstone tables. Discards: retention_policies (operator policies); retention_reports; retention_tombstones (the deletion record). |
| 0102 | **forward-only** | Adds runs.trace_state and the retention progress and dirty-day tables. After the retention job has ENFORCED anything, the downgrade drops the marker that tells readers a trace is gone, so deleted runs would look like ordinary runs with no events, and the progress records are lost. Reversible only while ABB_RETENTION_MODE never reached enforce; otherwise rollback = restore the pre-upgrade backup. **Upgrade:** CHECK validation scans runs under a short lock, and the new column has a constant default (a catalog change, not a rewrite). |
| 0103 | reversible | Adds analytics_runs_daily.expired_trace_runs (derived; a day rebuild refills it). Discards: analytics_runs_daily.expired_trace_runs. |
| 0104 | reversible | Adds projects.purge_state and workspaces.purge_state, the platform purge_tombstones table and the retention role's purge privileges. The downgrade discards the purge record and would un-hide a project or workspace that a purge left in the purging state: do not roll back while a purge is running (finish it, or restore the pre-upgrade backup). Discards: purge_tombstones (the record of purges that ran); purge_state markers. |
| 0105 | reversible | Grants the runtime role SELECT on alembic_version (read-only) so /readyz can compare the schema revision with the code's head. The downgrade restores the revoked state. |
