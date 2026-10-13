# Phase 13: Security observability and redaction modes

Status: Implemented; pending review (2026-10-13; steps 1-4 built, evidence below; step 5 is the independent review, fixes and the PR). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-13).
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
