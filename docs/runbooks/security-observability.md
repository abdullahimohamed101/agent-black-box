# Runbook: security observability (Phase 13)

For operators of a deployment. Design: ADR-130 (server-side redaction and capture modes), ADR-131 (detectors and findings, with Amendment 1),
`docs/SECURITY.md`. Findings mechanics (the job, `rebuild-findings`): `docs/runbooks/findings.md`.

## What the server does with what an SDK sends

Before an event or an artifact is stored the server scans every string it carries and replaces secret-shaped values with
`[REDACTED:<kind>]`. What it keeps depends on the project's **capture mode**:

| Mode | Stored | Dropped even if the SDK sent it |
| --- | --- | --- |
| `FULL` | attributes, tags, inline payload, artifact bodies, after redaction | nothing (a value that cannot be scanned is dropped, see below) |
| `METADATA_ONLY` (the default) | attributes and timings; commands, paths and URLs stay (they are metadata in the SDK's sense) | inline payload, `payload_ref`, artifact bodies and the attributes that point at them (`diff.artifact`, `shell.stdout_artifact`, `shell.stderr_artifact`) |
| `DISABLED` | as `METADATA_ONLY` | and free text: `error.message` and `metadata.*` attributes |

A stored event records what the server did in `redaction.server_count`, `redaction.server_kinds` (kind names such as `aws_access_key`,
never values) and `redaction.dropped_reason` (`mode_metadata_only`, `mode_disabled`, `scan_failed`, `too_large`). Artifacts carry the same record
in `artifacts.metadata.redaction`; a dropped body is replaced by a one-line marker. **Fail closed:** a value the scanner errors on, runs
out of time on, or that is over its budget is dropped and the reason recorded; the event keeps its metadata. Redelivery is idempotent
(the scan is deterministic).

## The default changed: METADATA_ONLY

**Behaviour change for existing deployments.** Before Phase 13 the server stored whatever the SDK sent (the SDK's own mode decided).
Now the server decides per project, and a project **with no settings row** is `METADATA_ONLY`. After you deploy, a project whose
SDKs send payloads or artifact bodies **stops storing them** until you set `FULL`. Nothing already stored is changed or removed (INV-1);
old payloads stay as they were, including any secret the SDK missed at the time.

Set a project to FULL (audited as `security.settings.update`):

```text
python -m abb_api.cli set-capture-mode --workspace W --project P --mode full        # metadata_only | full | disabled
python -m abb_api.cli create-project --workspace W --name N --slug S --capture-mode full
```

or, as a workspace owner or admin, Settings, Security, pick the project, Capture mode, Save (`PUT /v1/projects/{id}/security-settings`).
A new mode applies to the next accepted batch only. List what is set: Settings, Security, or
`SELECT p.slug, s.capture_mode, s.updated_by FROM projects p LEFT JOIN project_security_settings s USING (workspace_id) ...`
(no row means the default, `METADATA_ONLY`).

## Budgets (environment)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ABB_SECURITY_SCAN_EVENT_MAX_SECONDS` | 1 | time one event's strings may take to scan; beyond it the value is dropped (`scan_failed`) |
| `ABB_SECURITY_SCAN_ARTIFACT_MAX_BYTES` | 4 MiB | an artifact larger than this is stored as a marker (`too_large`); it is also the hard limit for scanning |
| `ABB_SECURITY_SCAN_ARTIFACT_MAX_SECONDS` | 10 | time one artifact may take to scan (`scan_failed`) |
| `ABB_SECURITY_REANALYSE_MAX_RUNS` | 200 | a mute or allowlist change re-analyses at most this many of the project's newest runs; 0 turns it off |
| `ABB_SECURITY_REANALYSE_MAX_AGE_DAYS` | 30 | ...and only runs that started within this many days |

Watch the logs for `artifact redacted` (counts and kinds only) and, in the database, for `redaction.dropped_reason` values `scan_failed` and
`too_large`: a steady stream of them means the budget is too small for the traffic, or an SDK is sending huge values.

```sql
-- what the server dropped or redacted in the last day, by reason
SELECT attributes->>'redaction.dropped_reason' AS reason, count(*) FROM events
WHERE received_at > now() - interval '1 day' AND attributes ? 'redaction.dropped_reason' GROUP BY 1;
```

## Findings and settings changes

- A **mute**, an **unmute** or an **allowlist** change queues the project's recent runs for re-analysis in the same transaction as the audit row.
  Recent runs first; one pending job per run, after `ABB_FINDINGS_DELAY_SECONDS`; the findings change within seconds without a manual rebuild. A
  capture mode change does not re-analyse (it governs future events).
- Runs older than the bound keep their findings until `python -m abb_api.cli rebuild-findings --workspace W --project P [--since T] [--limit N]`.
  The same command re-derives after a detector version bump (stored analyses read `outdated`).
- **Re-summarise and rebuild notes.** `rebuild-costs` and `search-rebuild` do not change security findings. `rebuild-findings` can be run any
  time: findings are derived from the events (INV-2) and a rebuild reproduces the same finding ids and evidence (checked in
  `scripts/security-e2e.sh`). Deleting rows from `findings` by hand is safe for the same reason.
- A destination finding needs an allowlist: with none configured the detector reports nothing (there is no honest baseline).

## What it does not do

- It does not scrub events already stored. A secret an old SDK sent in `FULL` mode before Phase 13 is still in that event; export
  and retention are Phase 19.
- Detection is pattern based (KI-132): obfuscated or indirect commands, secrets of unknown shape and values an SDK redacts into an artifact before the
  server sees them are not findings (KI-134); the finding says what the server or SDK marked, not everything an agent did.
- It never blocks or pauses an agent (Phase 14).

## Checking a deployment

`make security-e2e` runs the whole story against a scratch database: a planted run through the SDK, a client that skips redaction, a scan of every text
column and the artifact directory, the three modes, a rebuild, a settings change and a browser as owner, viewer and billing. It never touches the
shared databases. To check a live deployment by hand: send an event with an AWS-style key (`AKIA` plus 16 capitals) in a command to a `FULL` project
and read the stored event: the key is `[REDACTED:aws_access_key]` and `redaction.server_count` is 1.
