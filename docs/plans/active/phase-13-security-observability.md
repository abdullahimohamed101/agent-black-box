# Phase 13: Security observability and redaction modes

Status: Planned (2026-10-13). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-13).
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

