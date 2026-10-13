# Phase 13: Security observability and redaction modes

Status: Implemented; review fixed (2026-10-13; steps 1-4 built, step 5 the independent review `docs/plans/active/phase-13-code-review.md` found P1 1, P2 4, P3 10, every finding is dispositioned below; evidence below; the PR needs the user's approval). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-13).
Owner: api + web (+ sdk-compatible attribute contract). Branch: `feature/phase-13-security-observability` (from `main` `c25beaf`).
Depends on: Phase 6 (command risk classes, SDK redaction), Phase 10 (findings framework), Phase 15 (roles, withholding, audit log), Phase 16 (search oracle rules), Phase 22 (run page).
Spec refs: §32, §83, §93 (data classes, layered redaction), §62 invariants (INV-1 immutable events, INV-2 rebuildable derived state, INV-3 tenancy, INV-5 sensitive capture opt-in, redact before export).

## Outcome

After a run, an engineer or lead can see whether the agent touched secrets, ran something destructive or contacted somewhere unexpected, with every claim linked to the events that show it and no content in the claim. The server is a real second layer of redaction: a secret that the SDK missed is never stored, and a project can be configured so that no payloads are stored at all.

## Non-goals

- No blocking, pausing or approvals (Phase 14); observation only.
- No prompt-injection, unusual-tool-usage or excessive-permission detectors; no ML detection.
- No retroactive scrubbing of events already stored (INV-1); old payloads stay as they are and the docs say so.
- No external alerting or SIEM export (Phase 17 and later); no retention or deletion changes (Phase 19).

## Decisions (settled with the user)

- D1 Primary job: after a run, "did this agent touch secrets, run something destructive, or contact somewhere unexpected", each claim linked to evidence. Security findings sit beside the reliability findings in the Phase 10 framework (labelled "probable", severity, evidence links, rebuildable). A workspace-level Security view lists recent findings. Observation only. The redaction backstop and capture modes are the second pillar.
- D2 A finding states only the category, with fixed wording per detector, a severity, a count and event links; it never contains a path, command, hostname or secret value. Detection runs server-side over the full events regardless of role. Findings are identical for every role with run access (metadata). The evidence link opens the event, where the normal role rules apply (an owner sees the path or command, a viewer sees "Content withheld"). Findings are no oracle for withheld content: no query or facet over content for roles that cannot read it (ADR-101).
- D3 Six deterministic, versioned detectors, each with a fixed severity and a plain-words statement of the rule that matched: secret-file read (path matches secret-bearing names such as `.env*`, `id_rsa`, `.aws/credentials`, `.npmrc` tokens; high); credential in output (the server-side scan finds a secret-shaped string in captured output, diffs or prompts; names the kind such as "AWS-style key", never the value; critical; this is the finding side of the KI-032/KI-042 backstop); destructive command (R3/R4 from the classifier, re-checked server-side; high or critical); risky git (force push, history rewrite, push to a protected branch name; high); privileged database action (DDL, DROP, DELETE without a filter in visible SQL; high); unexpected destination (host not on the project's allowlist; medium; with no allowlist configured it reports nothing instead of guessing a baseline). A workspace owner can mute a detector or add an allowlist entry; both are audit-logged. Left out: prompt-injection indicators, unusual tool usage, excessive permission requests.
- D4 Server-side protection happens before storage because accepted events are immutable. At ingestion the server scans every incoming payload attribute and artifact; a secret-shaped value the SDK missed is replaced with `[REDACTED:kind]` before the event is stored. The stored event records that the server redacted something (a count and kinds, never the value), which feeds the credential-in-output detector. Fail closed: if the scanner errors or an artifact is too large to scan within its budget, the payload is dropped (the event keeps its metadata) with a marker saying why. Capture modes are enforced per project on the server, using the SDK's names and meanings so there is one vocabulary: FULL (payloads stored after redaction), METADATA_ONLY (payload attributes and artifacts dropped even if an SDK sends them), DISABLED (METADATA_ONLY plus no free text such as error messages and run metadata, the SDK's strongest minimisation mode). Refinement of the grill wording ("DISABLED accepts no events"): DISABLED keeps the SDK's existing meaning so the two layers do not disagree; stopping a project from ingesting at all is a separate, existing concern (API-key revocation). Events already stored stay as they are.
- D5 UI: run page gets a quiet chip beside the status ("2 security findings", only when present) and a separate "Security" group above the reliability findings in the Context column (severity glyph and word, plain-language category, "Show evidence"); replay does not show them. A workspace "Security" page in the rail lists recent findings (severity, category, run, project, when) with facets for severity, category and project; rows link to the run with that finding's evidence open; a project Security tab is the same view pre-filtered. Severity is carried by glyph and weight, never a new hue, and red stays reserved for run failure (ADR-111): Critical is a filled shield glyph and the word "Critical", High an outlined shield in amber, Medium and below muted ink. Clean state: "Nothing suspicious found" naming the detectors that are on. Owners get a Security settings page: capture mode per project, detector mutes, destination allowlist; every change is audit-logged.
- D6 Acceptance gates: (1) a planted-secret run (AWS-style key, private key, `.env` read, `rm -rf`, force push, `DROP TABLE`, off-allowlist host) yields exactly the expected findings, each linked to evidence, none containing content, identical for owner and viewer; (2) an SDK that skips redaction sends a secret and a scan over events and artifacts finds it nowhere, and a scanner failure or oversized artifact drops the payload and stores a marker; (3) a project in METADATA_ONLY stores no payload even when the SDK sends one, DISABLED also stores no free text; (4) precision: every existing fixture run and a corpus of benign realistic commands and paths yield zero security findings; (5) viewer and billing roles cannot learn paths or hosts from findings, search or facets; (6) findings are produced by a job and rebuildable from events with a CLI (INV-2); (7) scans have a size and time budget and ingestion latency does not regress beyond a measured bound.

## Contract between the parts (so they can be built in parallel)

- The server adds these attributes to an event it has redacted or stripped, registered in the event-schema attribute catalogue as metadata (not content): `redaction.server_count` (int), `redaction.server_kinds` (list of kind names such as `aws_access_key`, `private_key`, never values), `redaction.dropped_reason` (one of `mode_metadata_only`, `mode_disabled`, `scan_failed`, `too_large`, absent otherwise).
- Findings reuse the Phase 10 finding contract with category `security` and detector ids prefixed `sec.`; evidence is event ids only; `message` is the fixed wording.
- Per-project settings (capture mode default METADATA_ONLY, detector mutes, destination allowlist) live in a new table behind a repository, workspace and project scoped, audit-logged.

## Known issues considered

Pulled in: KI-032 (SDK detection best-effort; server backstop), KI-042 (server does not scan artifacts), KI-078 and KI-080 (unregistered attributes not classified; new `redaction.*` and security attributes must be registered and classified). Kept deferred: KI-040 (artifact quotas, Phase 19), KI-041 (S3 store, Phase 18).

## Acceptance criteria

The seven gates of D6, each evidenced with a command and its observed output, plus: no existing test weakened (modified tests listed with reasons); authz matrix, route registry and withheld-content classification updated and green; `scripts/quality.sh full` green in CI; adversarial review with no open P0/P1.

## Verification plan

Unit tests per detector (positive, negative, edge), the planted-secret and precision corpus tests, ingestion tests for redaction, modes and fail-closed, authz and cross-workspace tests, a rebuild test, a performance test, web component tests and fixtures Playwright, an API-backed `security-e2e.sh` script (planted run through the real SDK, viewer and owner browser checks), my own gate run, then a Fable review including a security pass.

## Risks

- Redaction at ingestion changes accepted events: it must be idempotent and deterministic so at-least-once redelivery of the same event stays idempotent on `(workspace_id, event_id)`.
- Scanner cost and ReDoS: bounded input sizes, bounded time, linear-time patterns, tested with adversarial input.
- False positives erode trust: the precision gate and the stated-rule wording are the control.
- Detectors read content the viewer cannot: the finding text must never echo it (test by sentinel strings).

## Ordered steps (one commit each)

1. Server scanner and redaction at ingestion, capture modes enforced per project, settings table and migration, attribute registration, ADR.
2. Detectors, `security` findings category, job and rebuild CLI, API (per-run, workspace list, settings, mutes, allowlist), authz and audit, ADR.
3. Web: run chip and Security group, workspace Security page, Security settings, glyphs, empty states.
4. Verification suite: planted-secret e2e through the real SDK, precision corpus, modes, performance, roles; docs and closing evidence.
5. Review, fixes, push and PR.

## Progress

- Step 1 (2026-10-13): built on this branch. ADR-130; `project_security_settings` (migration 0090), `abb_api/security/` (scanner, redaction, settings), ingestion and artifact
  enforcement, `redaction.*` registered, CLI `set-capture-mode` / `create-project --capture-mode`. Security-sensitive (tenant scoping, redaction, audit): review pass is step 5.
  One deviation from the plan text, recorded in ADR-130 (4): METADATA_ONLY drops payload and artifact references, not `CONTENT_ATTRIBUTES`.
- Step 2 (2026-10-13): built on this branch. ADR-131; detectors `sec.*` (`findings/detectors/security.py`, rules in `security/rules.py`, wording in
  `findings/security_words.py`), `DETECTORS_VERSION` 2, migration 0091 (severities `medium`/`high`, `security_count`/`security_max_severity`, partial index),
  `GET /v1/security/findings`, `GET /v1/security/detectors`, `GET|PUT /v1/projects/{id}/security-settings`, `RunOut.security_findings`,
  `FindingOut.category`. Two refinements of the plan text, both in ADR-131: a recorded R3 is reported only when the server re-check agrees (R3 includes
  `rm file`), and the reliability badge (`RunOut.findings`) stays reliability-only with the security count beside it. BILLING cannot read runs, so it
  cannot read security findings (403); every role that can read runs gets identical bodies. Known issues KI-132 (pattern based), KI-133 (settings apply on
  re-analysis). The generated web client changed: `severity` gains `medium`/`high` and `category` is required, which breaks 8 type errors in
  `apps/web/src/fixtures/findings.ts` and `apps/web/src/lib/findings.ts` until step 3. Security-sensitive (content read by detectors, tenant scoping, authz):
  review pass is step 5.
- Step 3 (2026-10-13): built on this branch (web only). Run chip and Security group, `?finding=` deep link, `/w/{ws}/security`, `/w/{ws}/settings/security`,
  fixtures (three runs, workspace list with facets and paging, detectors, in-memory settings with PUT through the proxy), glyph and contrast tests,
  fixtures Playwright (`e2e/security.spec.ts`), screenshots in `docs/screenshots/phase-13/`. Needs API-backed browser checks in step 4.
- Step 4 (2026-10-13): end-to-end evidence and one product fix, built on this branch. (A) KI-133 resolved: a mute, unmute or allowlist change queues the project's newest
  runs (default at most 200, within 30 days; `ABB_SECURITY_REANALYSE_MAX_RUNS` / `_MAX_AGE_DAYS`) for re-analysis in the audit row's transaction (ADR-131 Amendment 1).
  (B) `scripts/security-e2e.sh` (`make security-e2e`, CI job `security-e2e`) with `security_e2e_plant.py`, `security_e2e_check.py`, `security_bench.py` and
  `apps/web/e2e/security-real.spec.ts`. The run found one product defect, fixed in the step: an artifact's **name** was stored unscanned (`ServerRedactor.label`,
  ADR-130 Amendment 1). It also found KI-134 (a credential an SDK redacts into an artifact is not a finding). (C) the ordinary-run gate. (D) docs: runbook
  `docs/runbooks/security-observability.md`, benchmark `docs/benchmarks/phase-13-security.md`, `docs/TESTING.md`, KNOWN_ISSUES (KI-032, KI-042, KI-133 resolved).

- Step 5 (2026-10-13): the independent review (`phase-13-code-review.md`: P0 0, P1 1, P2 4, P3 10) and its fixes, one failing test first for each. The server and the
  SDK patterns now redact only a `user:password` argument and names that end in a secret word; a long line is cut only where no secret straddles the cut; look-alike
  characters are folded for detection; settings-triggered re-analysis is rate limited; the findings job reads artifacts through `ArtifactRepository`; the settings copy says
  what METADATA_ONLY keeps; ADR-130 Amendment 2, ADR-131 Amendment 2; KI-135 to KI-139. See "Code review dispositions".

## Closing evidence (step 4, 2026-10-13)

Evidence vocabulary: VERIFIED = a command was run in this environment and its output observed. Everything below is VERIFIED unless marked otherwise. Run on macOS
(Apple M5 Pro), Postgres 16 in Colima, Chrome for Playwright. The end-to-end run: `make security-e2e` (shown: the final run, database `abb_p13_security_final`, exit 0,
157 s, Playwright 7 passed). The assertions live in `scripts/security_e2e_check.py`; each prints one JSON evidence line.

| Gate | Command | Observed |
| --- | --- | --- |
| 1 planted run | `scripts/security-e2e.sh` (`security_e2e_plant.py planted`, then `security_e2e_check.py gate1-verify`) | The run recorded by the real SDK and `CodingRecorder` (a `.env` read, an AWS-style key and a private key in a command, its output and a file diff, `rm -rf /`, `git push --force origin main`, `psql -c 'DROP TABLE users_<tag>'`, a host off the allowlist; harmful commands recorded as spans, never executed) yields **exactly six** findings: `sec.credential_in_output` critical, `sec.destructive_command` critical, `sec.secret_file_read` high, `sec.risky_git` high, `sec.privileged_db` high, `sec.unexpected_destination` medium. 10 cited events resolve for the owner and have the right event types; each finding's title and message equal the fixed text (`describe`); no planted tag, key, PEM body, host, directory or command piece appears in any finding, facet or detector body of any role; run findings and the workspace list are **byte-identical** for owner, developer, viewer and a `runs:read` key; billing gets 403 on both; a viewer's run, events and spans bodies contain no planted value and a viewer's search for the tag, the host or `id_rsa` matches nothing; the 4 stored artifacts hold `[REDACTED:` markers and no key |
| 2 backstop | `security_e2e_check.py gate2-backstop`, `scan` | Raw HTTP "SDK that skips redaction" into a FULL project: command, payload (incl. a value under `password`), error message, artifact body **and artifact name** carry an AWS key and a PEM block. Stored: `redaction.server_count` 4, kinds `aws_access_key`, `denied_key`, `private_key`; artifact record `server_count` 2; an inline payload of 5,100 nodes is dropped as `too_large`. The credential finding cites the artifact and the kinds, never the value. Scan of **141 text/jsonb/tsvector/bytea columns of the whole database**, the 17 artifact files (1,666 bytes) and the server logs for 6 sentinels plus a PEM header: **0 found** (positive control: the plant tag is found in `events.attributes`, `artifacts.name` ..., so the scan can find things); repeated at the end of the script after the benchmark (28,576 rows) with 0 found. Strict API (nanosecond scan budget, 2 KiB artifact cap): the event's payload is dropped with `scan_failed` and nothing unscanned is stored; the artifacts are `["scan_failed", "too_large"]` and their bodies are markers |
| 3 modes | `security_e2e_check.py gate3-modes` | One payload-bearing run to each of three projects. FULL: 2 payloads stored, redacted (`[REDACTED:aws_access_key]`), free text kept, artifact body a redacted copy. METADATA_ONLY: 0 payloads, no artifact reference, artifact body a marker (`mode_metadata_only`), the API returns no payload text, free text kept. DISABLED: as METADATA_ONLY with reason `mode_disabled`, and no `error.message`/`metadata.*`. The metadata-only detectors still report in every mode (destructive command, risky git); the credential finding appears in FULL only |
| 4 precision | corpus: `uv run pytest tests/test_security_detectors.py -k corpus` (part of the API suite, which CI runs through `scripts/quality.sh full`); script: `gate4-benign` | Corpus: 3 passed (about 245 benign commands, paths, URLs and SQL; zero findings with and without an allowlist; at least 150 entries). Script: an ordinary SDK coding run (58 events, 26 shell commands: files, git, tests, `rm -rf node_modules`, `git push origin feature/...`, an allowed host, `psql ... WHERE`, `cat .env.example`) in the same FULL project with the same allowlist has **0** security findings and no chip. The fixture runs' zero-finding check is in the same suite (`test_the_benign_corpus...`, pathological and comparison fixtures) |
| 5 roles | `uv run pytest tests/authz/test_security_findings_access.py tests/authz/test_security_reanalysis.py tests/test_artifact_name_redaction.py`; `apps/web/e2e/security-real.spec.ts` | 26 passed. In the browser (real OIDC sign-in): a viewer sees the same six lines as the owner (equal inner text), the evidence drawer says "content withheld for your role", no planted value is in the DOM text, attribute values or HTML, and none of the 10+ API responses the viewer's browser received contains one; the viewer's Security page rows equal the owner's; viewer search shows no planted run; the viewer's settings page says "Not available for your role" and a real PUT through the proxy is 403; billing gets "Not available for your role" and no findings |
| 6 rebuild | `security_e2e_check.py gate6-rebuild` | The six findings plus the run's reliability finding (7 `findings` rows, 1 `run_findings` row) were deleted by SQL; `rebuild-findings --project full` brought back identical ids, evidence, wording and severities in 1.4 s. Then through the settings API as the owner, with **no manual rebuild**: an allowlist change removed `sec.unexpected_destination` in 1.6 s, a mute removed `sec.risky_git` in 1.6 s, unmuting and restoring the allowlist brought back all six, same ids, in 1.6 s; 5 audit rows (`security.settings.update`); a developer reading and a viewer writing the settings are 403 |
| 7 latency | `security_bench.py --assert` inside the script; A/B against `main` | `docs/benchmarks/phase-13-security.md`. 100-event batches (4,000 events, ~1 KB payloads): p50 55.4 ms, p95 63.5 ms in this run; against `main` (a `git worktree` of `c25beaf`, alternating 4 rounds each) medians p50 56.4 ms (main) vs 53.6 ms (branch), p95 70.2 vs 66.5 ms: no regression beyond noise. 10,000-event run: ingest 8-14 s on both; last ack to current 4.3 s (main) vs 5.9 s (branch), the findings job 472 ms vs 920 ms median. Bounds asserted (generous for shared runners): batch p50 250 ms, p95 600 ms, 10,000-event ingest and detection 60 s each. Scan budgets: forced and asserted in gate 2 (strict API) |

Browser (Playwright, `security-real.spec.ts`, 7 passed, web on :3190): owner signs in through the fake provider, sees the chip "6 security findings (most severe: Critical)" and the
Security group of six, opens the secret-file evidence and reads the path; the workspace Security page shows 10 rows, "Load more" pages on, the Critical facet narrows by URL, a row
opens the run with the finding marked and its evidence open (keyboard); the settings page saves two real PUTs through the same-origin proxy (200, new allowlist echoed), a real 422
`SETTINGS_INVALID` that does not repeat the value, a non-admin 403; axe (WCAG A/AA, no serious or critical) on the run page, the Security page and the settings in light and dark
with real data; screenshots `docs/screenshots/phase-13/real-*.png` (looked at: the viewer's evidence drawer, the Security page, the settings page in dark, the billing state).

### Gates run (lead, 2026-10-13, branch head before this commit)

| Check | Result |
| --- | --- |
| `apps/api`: `ruff check .`, `ruff format --check .`, `mypy` (no arguments) | all passed; 314 files formatted, 290 source files typed |
| `apps/api`: full `pytest` | **2414 passed** in 1,267 s (21:07) |
| `python -m abb_api.openapi --check`, `pnpm --filter @abb/web gen:api:check` | current |
| `apps/web`: `tsc --noEmit`, `eslint .`, `prettier --check .` | clean |
| `apps/web`: `vitest run` (1,229 tests) | passes; under load 1-5 tests per run time out at 5 s in unrelated files (eight full runs: 6, 5, 2, 1, 5, 2, 1, 1 failures in different files, e.g. Waterfall, evidence, replay, runs-list, context-fold, fixtures "are deterministic"; the first ran beside the API suite); every failing file passes alone (102 + 13 tests re-run). One was a real race in this branch's own test (`security-run.test.tsx` `?finding=`), fixed with `waitFor` |
| `apps/web`: `pnpm build` | passed |
| Fixtures Playwright (`pnpm test:e2e`) | 7 failures at full parallelism (a load flake: 4 files, each passes alone), then **247 passed, 83 skipped, 0 failed** with `--workers=2` |
| API-backed E2E scripts (sequential, `abb_p13_*` databases) | auth 17 passed, findings 6, multiagent 6, coding 6, stream 5, eval 7, analytics 4, integrations (passed, no browser), **security 7**: all exit 0. Ingestion changed in step 1, so every one is a regression check; none needed a re-run |
| `packages/event-schema`, `packages/sdk-python` | not touched in step 4 (step 1 changed event-schema; its suite ran then); not re-run here |
| `scripts/quality.sh full` | UNVERIFIED (env) as a single command here; its parts were run separately as above. CI runs it |

### Gates run after the review fixes (step 5, 2026-10-13, branch head after the last fix; VERIFIED)

| Check | Result |
| --- | --- |
| `apps/api`: `ruff check .`, `ruff format --check .`, `mypy` (no arguments) | all passed; 316 files formatted, 292 source files typed |
| `apps/api`: full `pytest` (database `abb_p13_fix`) | 2626 passed, 4 failed in 1,438 s. Cause of each: `test_comparison_align::test_the_fixture_is_current`, the recorded compare fixture held `git push -u [REDACTED:cli_user]` (the P1-1 bug; regenerated, 90 comparison and detector tests pass); `test_openapi::test_the_committed_file_is_current`, the route description was edited while the suite ran (re-run in the next row, passes); `test_findings_job::test_a_losing_job_that_is_not_pending_enqueues_a_delayed_successor`, a timing flake under load (passes alone); `test_stream_reauth::test_a_revoked_or_expired_session_ends_its_stream`, **not caused by this branch**: it revokes sessions with `expires_at > now() + 1 day` while its fake clock is 2026-10-07 (168 h sessions), so it fails on any run after 2026-10-13 12:00 UTC (fails alone, deterministic; the lead's earlier 2414 passed ran before that moment). Not touched here; see the state file |
| `apps/api`: re-run after the last edits: `test_openapi`, comparison, `test_findings_job`, `test_migrations`, `tests/authz` | 322 passed in 642 s |
| `python -m abb_api.openapi --check`, `pnpm gen:api:check` | current (exit 0) |
| `packages/sdk-python`: `pytest`, `ruff check`, `ruff format --check`, `mypy` | 922 passed (893 before; 29 added); ruff and format clean; mypy 31 files |
| `packages/event-schema` | not touched; not re-run |
| `apps/web`: `tsc --noEmit`, `eslint .`, `prettier --check .` | clean |
| `apps/web`: `vitest run` | 1,235 passed, 1 failed (`replay-run.test.tsx` "Space on the timeline plays", a 5 s load timeout; the file alone: 25 passed) |
| `apps/web`: `pnpm build` | passed |
| Fixtures Playwright (`pnpm test:e2e --workers=2`) | **247 passed, 83 skipped, 0 failed** (3.7 min). Screenshots the run rewrote were restored except the settings pages whose copy changed |
| API-backed E2E scripts (sequential, databases `abb_p13_fix_*`) | security 7 passed, coding 6, auth 17, findings 6, analytics 4, integrations passed; multiagent 6, stream 5 and eval 7 passed on a second run: the first run of each failed one timing-sensitive check (a drawer-closing check, the p95 spec at its 30 s test timeout, the human-verdict case) under machine load, the cases KI-127 describes |

## Code review dispositions (step 5)

Every finding of `docs/plans/active/phase-13-code-review.md`, with the fix, the test that failed before it, and where it is recorded. "Fixed" means a test failed on the old code and passes now.

| Finding | Disposition | Fix and test |
| --- | --- | --- |
| P1-1 `-u`/`-U` redacted after any program | Fixed | `cli_user` (server and SDK, same commit) redacts only a `user:password` value (uid:gid pairs excepted). `test_ordinary_commands_with_a_u_flag_are_stored_verbatim` (16 commands incl. `python -u`, `pip install -U`, `sort -u`, `docker run -u 1000`), `test_a_user_password_credential_argument_is_still_redacted` (10 forms incl. mysql `-p`, docker login), `test_a_u_flag_does_not_blind_the_secret_file_detector` (`sort -u .env` reaches `reads_secret_file`); SDK `test_ordinary_commands_and_assignments_are_left_alone`; the parity corpus carries them. ADR-130 Amendment 2 (2); residual `docker run -u root:root` is KI-135 |
| P2-1 assignment patterns redact ordinary text | Fixed | A name is a credential only when it ends in a secret word or a qualified `key` (segments, camel case); `PWD`, `MONKEY`, `primary_key`, `cache_key`, `"path"`, `"author"` stay. `test_assignments_that_are_not_credentials_are_stored_verbatim` (27 texts), `test_assignments_of_real_secrets_are_still_redacted` (39), both in the SDK parity corpus; SDK in the same commit. ADR-130 Amendment 2 (3), `docs/SECURITY.md`, runbook; residual (a name ending in `token` is redacted whatever it holds) KI-135 |
| P2-2 settings copy overstates METADATA_ONLY | Fixed | `lib/security.ts` hints: Metadata only keeps commands, file paths, URLs, error messages, run metadata, timings and counts and drops inline payloads, diffs and artifact bodies; Disabled also drops error messages and run metadata. `security-settings.test.tsx` (the two copy assertions changed, see below). Screenshots refreshed. Gate 3's "no payloads" reads as `payload`, `payload_ref`, artifact bodies and the attributes that point at them |
| P2-3 determinism claim vs the time budget | Fixed (docs and signal) | ADR-130 §1 corrected and Amendment 2 (1): the time budget is the one non-deterministic path, a load-induced `scan_failed` then a clean redelivery is a `conflict`, first stored version wins; per-event budget kept on purpose. The `server redaction applied` log line counts `scan_failed` and `too_large` apart (`test_the_log_line_counts_time_budget_and_size_drops_apart`); runbook and SECURITY.md say to watch it |
| P2-4 findings reads the `artifacts` table | Fixed | `ArtifactRepository.redaction_kinds` (tenant scoped) used by `findings/security_inputs.py`. `tests/test_artifact_redaction_kinds.py` (run scoping, kind filter, limit, other workspace and project see nothing, and a test that no file under `findings/` names `t.artifacts`). The runs/projects listing join stays, recorded in ADR-131 Amendment 2 |
| P3-1 window cut leaks a quoted value's tail | Fixed | `_seam_is_safe` / `_safe_cut`: a cut is used only if scanning 4,096 characters either side whole and in two pieces agrees; else the next candidate, else `ScanTooLarge`. `test_a_secret_that_straddles_a_window_cut_is_still_redacted` (quoted value, `DB_PASSWORD='..'`, `Bearer` token, `mysql -p`, `Cookie:`: all leaked before), `test_a_cut_that_cannot_be_made_safe_fails_closed`. Residual KI-138 |
| P3-2 quoted marker redacted again | Fixed | `(?!["']?\[REDACTED)` in the assignment, flag and quoted-key patterns, both layers. `test_a_quoted_marker_is_not_redacted_again_or_counted`, SDK `test_a_quoted_marker_is_not_redacted_again` |
| P3-3 mode read and store not one transaction | Documented (accepted) | ADR-130 Amendment 2 (7): the window is the batch's processing time and reading the mode inside the ingest transaction would not remove it; contract stays "the next batch"; KI-139 |
| P3-4 settings PUT has no rate limit | Fixed | Coalescing was verified (`test_repeated_edits_coalesce_into_one_job_per_run` passes unchanged); a per-project cooldown (`ABB_SECURITY_REANALYSE_COOLDOWN_SECONDS`, default 60, 0 off) defers a later wave to the end of the cooldown (not dropped; previous change read from the audit log). `test_a_second_wave_within_the_cooldown_is_deferred_not_dropped`, `test_the_cooldown_is_per_project_and_a_mode_change_does_not_start_it`, `test_a_zero_cooldown_switches_the_rate_limit_off`. ADR-131 Amendment 2, runbook, OPERATIONS, `.env.example`, api-v1, OpenAPI |
| P3-5 host pattern 100 characters, no IDN hint | Fixed (one part not done) | 253 characters (plus `*.`), validation message in the API, the web and the fixtures says to use punycode, audit entries shortened to 100 characters (`test_a_host_name_may_be_as_long_as_a_dns_name_and_no_longer`, `test_a_non_ascii_host_is_refused_with_a_punycode_hint`, `test_the_audit_row_of_long_allowlist_entries_stays_within_the_audit_limits`, web `host patterns mirror the API`). A warning for a one-label wildcard (`*.com`) is not built: it is the owner's choice and the effect is stated in the allowlist description |
| P3-6 look-alike and zero-width characters | Fixed (partly; residual filed) | A non-ASCII line is folded (zero-width removed, NFKC) for detection only; a hit replaces that line with the markers found, every other line is stored as sent. `test_a_secret_hidden_by_look_alike_characters_is_redacted_line_wise` (zero-width space, word joiner, BOM, full-width), `test_non_ascii_text_that_is_not_a_secret_is_stored_as_it_was`. Other confusables, URL encoding and base64: KI-136 |
| P3-7 `url_token_user` redacts long user names | Fixed | needs a digit, an underscore or mixed case; `test_a_long_username_without_a_password_is_not_a_credential`, `test_a_long_token_shaped_user_is_still_redacted`, SDK in step |
| P3-8 0091 index not CONCURRENTLY | Documented | migration docstring (locking, the 0041 / KI-056 precedent, downgrade), runbook; KI-137 (S3, Phase 19) |
| P3-9 benchmark bounds load-sensitive | Fixed | p95 1,000 ms and every bound scaled by a calibration read (`REFERENCE_FLOOR_MS`; never scaled down): `tests/test_security_bench_bounds.py` |
| P3-10 parity test compared four attributes | Fixed | `test_every_attribute_is_kept_or_dropped_as_the_sdk_does` runs every attribute of `KNOWN_ATTRIBUTES` plus unknown and `metadata.*` keys through the SDK and the server in each mode (the one deliberate difference, the artifact-pointer attributes, is asserted separately); `test_the_free_text_the_disabled_mode_drops_is_exactly_what_the_sdk_drops` names `error.message` and `metadata.*`. A mutation of the server's DISABLED list fails it |

## Extra acceptance items

- No existing test weakened: see "Modified existing tests". The authz registry, role matrix, route-completeness and withheld-content tests list the four security routes and the new attributes (VERIFIED: part of the 2414).
- `scripts/quality.sh full` green in CI: UNVERIFIED until the PR runs (see above).
- Adversarial review with no open P0/P1: pending (step 5). Security-sensitive parts for the reviewer: server redaction and the scan budgets, tenant scoping of the settings and the re-analysis jobs, the content-free finding contract, the artifact-name fix.

## Modified existing tests

Every test or script that exists on `main` and was changed by steps 1-4 (`git diff main...HEAD --name-status` over `apps/web/tests`, `apps/web/e2e`, `apps/api/tests`, `packages/*/tests`, `scripts`, `examples/*/tests`, `integrations/*/tests`; additions of new files are not listed). None was weakened, skipped or deleted.

| File | Change and why |
| --- | --- |
| `apps/api/tests/ingest_helpers.py` | `make_tenant(..., capture_mode=FULL)`: test projects capture FULL because the product default is now METADATA_ONLY (ADR-130); `None` keeps the default; all existing callers unchanged |
| `apps/api/tests/search/test_search_http.py` | the seeded command carries `Bearer s3cr3t`; the server now redacts it before storage, so the old term finds nothing and the marker `redacted` is what a payload reader can search: the assertion states the new fact (the old term matches no run; the marker matches the same run) |
| `apps/api/tests/test_detectors.py` | `DETECTORS_VERSION` 1 to 2 (the security detectors, ADR-131) |
| `apps/api/tests/test_openapi.py` | the operation count includes the four security routes (`32 + 15 + 1 + 4`), and the new routes are listed |
| `apps/api/tests/authz/registry.py`, `test_registry_is_complete.py`, `test_role_matrix.py` | the four routes are registered in the route-walking registry and the role matrix (additions; one count line) |
| `apps/api/tests/authz/test_content_withheld.py` | additions: the new `redaction.*` attributes and findings are metadata for every role |
| `apps/api/tests/test_cli.py`, `test_migrations.py` | additions only: `set-capture-mode`, `create-project --capture-mode`, `rebuild-findings` for security, migrations 0090 and 0091 |
| `apps/web/e2e/fixtures.spec.ts`, `apps/web/e2e/runs-list-redesign.spec.ts` | `RUN_COUNT` 13 to 16: the fixtures gained three security runs (step 3) |
| `apps/web/tests/app-shell.test.tsx` | the rail lists Security: the link list has five entries and the test names it |
| `scripts/analytics-e2e.sh`, `auth-e2e.sh`, `coding-e2e.sh`, `e2e-web-real.sh`, `eval-e2e.sh`, `findings-e2e.sh`, `integrations-e2e.sh`, `multiagent-e2e.sh`, `sdk-e2e.sh`, `smoke.sh`, `stream-e2e.sh` | `create-project ... --capture-mode full` (one line each): these scripts need payloads, and the default is METADATA_ONLY |
| `apps/api/tests/authz/test_security_reanalysis.py`, `apps/web/tests/security-settings.test.tsx`, `apps/web/tests/security-run.test.tsx` | not on `main` (new in this branch). Step 4 touched the last two: the settings test gained an assertion for the new "Existing runs are re-analysed in the background; recent runs first." note (no existing assertion changed), and the `?finding=` test waits for the column to open instead of racing it |
| `apps/web/e2e/security.spec.ts` (new in step 3) | unchanged in step 4; its settings screenshots in `docs/screenshots/phase-13/security-settings-*.png` were regenerated because the page gained the note |

Step 5 (review fixes) additions to the table above. No test was weakened, skipped or deleted; two changed an expectation, for the reason stated.

| File | Change and why |
| --- | --- |
| `packages/sdk-python/tests/test_redaction.py` (on `main`) | additions only (ordinary commands and assignments are left alone, credential-shaped names still redacted, a quoted marker): the SDK patterns changed in the same commit as the server's (review P1-1, P2-1, P3-2, P3-7); no existing assertion changed, all 893 earlier SDK tests still pass |
| `apps/api/tests/conftest.py` (on `main`) | `make_settings` sets `security_reanalyse_cooldown_seconds` to 0 by default, like the debounce settings next to it: tests change settings many times in a row and drain the worker, and the cooldown (default 60 s) would defer the later waves (review P3-4); the cooldown tests set their own policy |
| `apps/api/tests/authz/test_security_reanalysis.py` (new in this branch) | the two policies in `test_the_run_count_and_the_age_are_bounds_and_the_newest_come_first` get `cooldown_seconds=0`: the test is about the run and age bounds, and with the default cooldown the second wave is (correctly) deferred. Expectation unchanged |
| `apps/api/tests/test_security_scanner.py` (new in this branch) | `test_hostile_input_of_a_megabyte_scans_in_bounded_time` accepts `ScanTooLarge` as well as a result (still within 8 s): a line built so that every cut changes the result is now refused instead of cut blindly (review P3-1); 3 of the 22 inputs end that way. The time bound is unchanged |
| `apps/api/tests/test_security_scanner_parity.py` (new in this branch) | the corpus gained the new ordinary, benign and secret-shaped texts; the four-attribute comparison was replaced by one over every attribute (the old test still exists) |
| `apps/web/tests/security-settings.test.tsx` (new in this branch) | the two capture-mode copy assertions changed with the copy (review P2-2: "No payloads or artifact bodies are stored" became "Inline payloads, diffs and artifact bodies are not stored" plus what is kept; "no free text such as error messages" became "also no error messages or run metadata"), and the old "Timings, types and counts" sentence is asserted absent; host cases added (253 characters, punycode) |
| `apps/api/tests/fixtures/compare_pair/baseline.ndjson` (on `main`) | regenerated with `scripts/gen_compare_fixture.py --out`: the recorded SDK output had `git push -u [REDACTED:cli_user] fix/oauth-session-expiry` (review P1-1, the bug itself); it now reads `git push -u origin fix/oauth-session-expiry`. Two lines changed; `test_the_fixture_is_current` failed until regenerated and the comparison tests pass on it |
| `scripts/security-e2e.sh` | exports `ABB_SECURITY_REANALYSE_COOLDOWN_SECONDS=0`: the check changes one project's settings several times in a row and expects each re-analysis |

## Known issues (step 4 disposition)

KI-032 and KI-042 resolved (gates 2 and 3 passed; the residue, a secret of unknown shape in FULL mode, is inherent and stated); KI-133 resolved (ADR-131 Amendment 1); KI-078 and
KI-080 progress recorded (secrets in these fields are no longer stored; the integration free-text classification stays open); KI-132 stays; new KI-134 (a credential an SDK redacts
into an artifact is not a finding). KI-040 and KI-041 stay deferred (Phases 19 and 18).

## Findings of step 4 (what the end-to-end run taught)

1. An artifact **name** was stored unscanned: a raw upload with a key in its name put the key in `artifacts.name`. Fixed (`ServerRedactor.label`; `tests/test_artifact_name_redaction.py` failed before).
2. In METADATA_ONLY and DISABLED the payload is dropped before it is read, so a secret in it is neither stored nor counted and the credential finding needs FULL (or an SDK marker in an attribute). Documented in ADR-130 Amendment 1; the plan's gate 3 wording about "detectors still raise findings that only need metadata" holds for the command detectors.
3. The coding helpers mask the host's environment values by value and a secret file's values everywhere: a script that passes its sentinels through the environment hides them from the server. The plant script gets a clean environment and a JSON file.
4. KI-134, above.
5. Sign-in flake: once in about ten runs the fake provider's token exchange timed out (`ReadTimeout`, `login=unavailable`); the next run passed. Not a product defect found; recorded.
