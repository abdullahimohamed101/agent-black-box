# Phase 13 code review: security observability and redaction modes

Reviewer: independent, adversarial (review-change skill; security pass). Date: 2026-10-13.
Scope: `feature/phase-13-security-observability` against `origin/main` (`git diff origin/main...HEAD`, 20 commits, 162 files).
Context read: the plan (D1-D6, gates), ADR-130 (+Amendment 1), ADR-131 (+Amendment 1), `docs/SECURITY.md`, AGENTS.md invariants,
ADR-101, ADR-061/062, the runbook, KNOWN_ISSUES.

Evidence vocabulary: **VERIFIED** = run here and observed; **ASSUMED** = reasoned from code.

What I ran (VERIFIED):

- `uv run pytest tests/test_security_scanner.py tests/test_security_scanner_parity.py tests/test_server_redactor.py tests/test_security_rules.py` -> 424 passed, 2.65 s.
- `ABB_TEST_DATABASE_URL=.../abb_p13_review uv run pytest tests/test_security_settings.py tests/test_artifact_name_redaction.py tests/authz/test_security_reanalysis.py` -> 24 passed, 27.9 s (new database, created for this review).
- `uv run alembic heads` -> `0091 (head)`, a single head (the chain is 0060 -> 0080 -> 0070 -> 0071 -> 0090 -> 0091).
- `git ls-files | grep -i env` -> only `.env.example` is tracked; no `.env`, no generated AGENTS.md/CLAUDE.md, no screenshot churn outside `docs/screenshots/phase-13/`.
- A probe script against `SecretScanner` (scratchpad, not committed): the benign and adversarial inputs quoted below, plus timing of eight 50-60 K hostile strings (all under 15 ms).

Counts: **P0 0, P1 1, P2 4, P3 10.**

---

## P1 (must fix before merge)

### P1-1 The server now mutates ordinary commands in every project and mode: `-u`/`-U` after any program is redacted as a credential

- **Location:** `apps/api/src/abb_api/security/scanner.py:86-89` (`cli_user` pattern); mirrored in the SDK at `packages/sdk-python/src/blackbox/redaction.py:96`; locked in by `apps/api/tests/test_security_scanner_parity.py:55` (`mysql -u root ...`).
- **Problem:** `(?i)((?:^|\s)(?:-u|--user|--proxy-user)(?:\s++|=)["']?)(?!\[REDACTED)[^\s"']+` matches `-u` and (case-insensitive) `-U` after *any* program and redacts the next token. VERIFIED with the probe:
  - `python -u main.py` -> `python -u [REDACTED:cli_user]`
  - `pip install -U requests` -> `pip install -U [REDACTED:cli_user]`
  - `sort -u names.txt` -> `sort -u [REDACTED:cli_user]`
  - `docker run -u 1000 image` -> `docker run -u [REDACTED:cli_user] image`; `useradd -u 1000 bob` likewise.
- **Why this is P1 and not "known SDK behaviour":** before this phase the pattern lived only in the SDK, where a user can swap the redactor (`Redactor(callback=...)`) or use another client. Step 1 applies the server copy to `shell.command` (and every other string attribute) **in every capture mode, for every client, with no per-project opt-out**, before the immutable event is stored (INV-1: it can never be repaired). `python -u` is one of the most common agent commands; the run page, span names, the search index and the replay will show `[REDACTED:cli_user]` where the script name was. That contradicts the mission statement in AGENTS.md ("developers must first trust that Agent Black Box accurately shows what their agents did") and it also blinds the new detectors on the same text: `sort -u .env` becomes `sort -u [REDACTED:cli_user]`, so `sec.secret_file_read` cannot see the `.env` (VERIFIED by the probe; `rules.reads_secret_file` receives the stored text).
- **Failure scenario:** an engineer debugging a failed run opens `shell.command` and reads `python -u [REDACTED:cli_user]`; they cannot tell which script ran, and nothing in the UI explains that a non-secret was removed. The precision corpus (`tests/security_corpus.py:154` has `sort -u words.txt`) does not catch it because the corpus tests findings, not redaction output.
- **Suggested correction:** make `cli_user` require either a credential shape in the value (`[^\s"':]+:[^\s"']+`, i.e. `user:password`) or a program that takes `-u user` (`curl`, `wget`, `mysql*`, `mariadb`, `psql -U`, `docker login`, `podman login`, `git ... -u`?), and keep `--user`/`--proxy-user` as they are. Change the SDK pattern in the same commit so the parity test stays truthful, and add the four commands above to a scanner "benign commands are stored verbatim" test. (The SDK's version is pre-existing, so the SDK change is a bug fix, not a contract change; `[REDACTED:cli_user]` markers already stored stay, INV-1.)

---

## P2 (should fix)

### P2-1 Assignment-shaped patterns redact common non-secrets (`PWD=`, `MONKEY=`, `primary_key:`, `cache_key =`), now server-side without opt-out

- **Location:** `scanner.py:138-150` (`secret_assignment`, `secret_assignment_short`), `scanner.py:158-164` (`credential`); SDK equivalents.
- **Problem:** the name part allows any prefix (`[A-Za-z0-9_]{0,64}`) before `key|token|secret|...`, and `_short` matches `pwd|pass|pat` as a whole segment. VERIFIED: `PWD=/home/alice` -> `PWD=[REDACTED:credential]`; `pwd: /tmp/x` likewise (every `env`/`printenv` capture and every shell transcript that prints the working directory); `MONKEY=banana` -> redacted; `primary_key: id`, `cache_key = f'{a}:{b}'`, `idempotency_key: abc`, `ssh_key: ~/.ssh/id_rsa`, `token: abc` all redacted. ADR-130 acknowledges "assignment-shaped kinds match ordinary code" for *findings* (they are not high-confidence), but the **stored text** is still rewritten in FULL payloads, diffs, artifacts and `shell.command`.
- **Failure scenario:** a diff artifact of a Django model (`primary_key=True`) or a YAML config (`cache_key:`) comes back with `[REDACTED:credential]` in place of values; the SDK's own "mask known environment values" layer already handled the real secrets; the engineer sees redactions they cannot explain and stops trusting the diff view.
- **Suggested correction:** (a) require the key word to be a whole identifier segment (`(?:^|[_.-])key` etc.) and exclude a small list of known non-secret names (`pwd`, `oldpwd`, `primary_key`, `foreign_key`, `sort_key`, `cache_key`, `partition_key`, `idempotency_key`, `public_key`, `ssh_key` when the value is a path); (b) or treat assignment-shaped kinds as *attribute-only* (commands) and skip them for payload/artifact bodies where the SDK already ran; (c) at minimum record this as a KI with S2 and an opt-out plan (a per-project "scan level" setting is the natural fix). Keep the SDK in step.

### P2-2 The capture-mode copy in the settings UI (and gate-3 wording) overstates what METADATA_ONLY withholds

- **Location:** `apps/web/src/lib/security.ts:250` ("Timings, types and counts are kept. No payloads or artifact bodies are stored."); plan D4/gate 3 ("a trace can be configured to store no payloads"); ADR-130 §4 is accurate.
- **Problem:** in METADATA_ONLY the server stores and shows (to `payload.read` roles) `shell.command` with its arguments, `file.path`, `http.url` (including query strings, which the scanner only partially covers), `error.message`, `metadata.*`, `tool.name`, tags and run names. "Timings, types and counts" is not what is kept; a workspace owner choosing the default on that description will believe commands and paths are not stored. The runbook (`docs/runbooks/security-observability.md:14`) says it correctly; the UI does not.
- **Suggested correction:** change the hint to "Commands, paths, URLs, timings and counts are kept; inline payloads, diffs and artifact bodies are not stored." and the DISABLED hint to "also no error messages or run metadata". Add the sentence to the plan's gate-3 evidence so the claim "no payloads" is read as `payload`/`payload_ref`/artifact bodies.

### P2-3 ADR-130's determinism claim does not hold under the time budget; the consequence (a `conflict` on redelivery) is undocumented

- **Location:** `apps/api/src/abb_api/security/scanner.py:254-256` (`check_time` raises `ScanFailed`), `redaction.py:204-218` (`_text` turns it into a dropped value with `scan_failed`), ADR-130 §1 ("Redaction is a pure function of the event and the mode").
- **Problem:** the per-event deadline (`ABB_SECURITY_SCAN_EVENT_MAX_SECONDS`, default 1.0 s, measured from `apply()` start) depends on host load. If a first delivery is slow (worker thread starved during a large batch), its payload is dropped with `scan_failed` and that version is stored; the SDK's retry of the same batch (network blip, 503 after commit, at-least-once) scans fine, produces a different redacted form, and `PgEventStore` reports `conflict`. The SDK counts it as rejected; the stored event has no payload although the client sent one and nothing says the drop was load-induced. Fail-closed is the right default, but the ADR calls the function pure and the runbook does not tell an operator to look for `scan_failed` spikes as a capacity signal.
- **Suggested correction:** document in ADR-130 §1/§8 that time-budget drops are the one non-deterministic path and that a redelivery is then a `conflict`; emit a counter/log line (already partly there: `server redaction applied`) that distinguishes `scan_failed` from `too_large`; consider measuring the budget per *value* rather than per event so one large attribute cannot starve the rest.

### P2-4 Module boundary: the findings module reads the `artifacts` table directly

- **Location:** `apps/api/src/abb_api/findings/security_inputs.py:26-37` (`select(t.artifacts.c.id, t.artifacts.c["metadata"]["redaction"]["server_kinds"]) ...`).
- **Problem:** AGENTS.md ("The API's modules ... talk through service/repository interfaces, not each other's tables") and INV-7. The query is workspace- and project-scoped (good), but it couples the detector job to the artifacts table layout and the `metadata.redaction` JSON shape owned by `artifacts/service.py`. `security_repository.py:104-113` also joins `runs` and `projects` from the findings module; that is a listing join and is more defensible, but it is the first such join in `findings/`.
- **Suggested correction:** add `ArtifactRepository.redaction_kinds(run_id, project_id, kinds, limit)` (one method, tenant-scoped) and call it from `load_security_inputs`; keep the runs/projects join but note it in ADR-131.

---

## P3 (optional)

### P3-1 Window cut inside a quoted value leaks the tail of the value (only for lines of 48 KiB+ without a newline)

- **Location:** `scanner.py:332-353` (`_windows` cuts after a separator in `_BREAKS`, which includes space), `scanner.py:139-143` (quoted values up to `_Q` may contain spaces).
- VERIFIED: a 64 KiB line ending `password = "abc def ghi"` is stored as `password = [REDACTED:credential] def ghi"` (the cut landed after `"abc`). Token-shaped kinds cannot straddle a space, so this affects only quoted assignment values in very long lines. Suggest cutting only at newline/`;`/`,`/`&` (not space or quote) or re-scanning the last `_Q` characters across the boundary.

### P3-2 A quoted SDK marker is re-redacted and counted

- **Location:** `scanner.py:98-104`, `139-150` (the `(?!\[REDACTED)` guard is checked before the optional opening quote).
- VERIFIED: `{"password": "[REDACTED]"}` -> `{"password": [REDACTED:credential]}` with `count == 1` (kind `quoted_key_value`); `password="[REDACTED]"` likewise. The structured-payload path (`redaction.py:249`) handles this, so it only inflates `redaction.server_count` for serialised JSON inside strings; no finding (not high-confidence) and idempotent on the second pass. Suggest allowing an optional quote inside the look-ahead: `(?!["']?\[REDACTED)`.

### P3-3 Mode read and store are not one transaction (TOCTOU)

- **Location:** `apps/api/src/abb_api/ingestion/service.py:191-196` (mode read on its own connection) vs the `engine.begin()` store that follows; `artifacts/service.py` likewise.
- A tightening of the mode between the read and the commit lets one in-flight batch store payloads under the old mode. The window is milliseconds and the ADR says "next batch"; acceptable. Document, or read the mode inside the ingest transaction.

### P3-4 Settings PUT has no rate limit; each mute/allowlist change can queue up to `ABB_SECURITY_REANALYSE_MAX_RUNS` (200, up to 5000) jobs

- **Location:** `apps/api/src/abb_api/security/settings.py:324-345`; `core/config.py` (`le=5000`).
- Coalescing works only while jobs are pending (VERIFIED by `test_repeated_edits_coalesce_into_one_job_per_run`); after the debounce they run, and the next PUT queues again. A scripted admin toggling a mute every few seconds sustains ~40 jobs/s per project on the shared worker (KI-104 territory). Bounded by an admin's own actions and audit-logged, so P3. Suggest a per-project cooldown (skip re-queue if the last settings-triggered enqueue is younger than the debounce) or counting toward the per-key limiter.

### P3-5 Host pattern limits: 100 characters (FQDNs allow 253) and no IDN handling

- **Location:** `settings.py:35,100-110`; `apps/web/src/lib/security.ts:223-240`.
- A non-ASCII host fails the pattern with the fixed message; the UI gives no hint to enter punycode. `*.` and `*` alone are correctly rejected (VERIFIED by reading the regex and `test_host_patterns_are_normalised_and_validated`). Note `host_allowed` compares `*.example.com` by suffix, so `*.com` is accepted and matches everything under `.com`; that is the owner's choice, but the UI could warn on a one-label wildcard.

### P3-6 Unicode confusables and zero-width characters evade the scanner (documented as best effort)

- **Location:** `scanner.py:286-292` (`normalize` strips NUL and ANSI only).
- VERIFIED: `AKIA​IOSFODNN7EXAMPLE`, full-width `ＡＫＩＡ...`, URL-encoded `AKIA%49...` and base64 pass through. KI-032's residue covers unknown shapes; stripping zero-width code points (U+200B-U+200D, U+FEFF) and NFKC-folding before the pattern pass would close the cheapest two at no risk to idempotency.

### P3-7 `url_token_user` redacts any 16+ character userinfo without a password

- **Location:** `scanner.py:136`. `https://my-long-service-account@git.example.com/repo` becomes `https://[REDACTED:url_credentials]@...`. Same class as P2-1; low frequency.

### P3-8 Migration 0091 builds the partial index inside the migration transaction

- **Location:** `apps/api/migrations/versions/0091_security_findings.py:52-57`.
- `ADD COLUMN ... DEFAULT 0 NOT NULL` is metadata-only on PG 11+, and the two CHECK swaps are quick, but `CREATE INDEX` on `findings` takes a share lock for the build. `findings` is small today; if an operator has millions of rows, follow the KI-056 precedent (`CONCURRENTLY` with `autocommit_block`). The downgrade deleting derived rows is acceptable under INV-2 (rebuildable) and says so.

### P3-9 The benchmark assertion is a CI gate with load-dependent bounds

- **Location:** `scripts/security_bench.py:31` (`batch_p50_ms 250`, `batch_p95_ms 600`), run by the new `security-e2e` CI job with two APIs, a worker, a fake provider and a web build on one runner.
- Measured 55-70 ms on an M5 Pro; the 8x headroom is probably enough, but it is the one assertion in CI that a slow runner can fail without a code change. Suggest making `--assert` log-only in CI (keep the numbers in the artifact) or widening p95 to 1,000 ms.

### P3-10 D4's "no free text" is narrower than the words suggest, and the parity test only compares four attributes

- **Location:** plan D4; `test_security_scanner_parity.py:111-127` (`survivors` checks `tool.name`, `error.message`, `metadata.owner`, `shell.command`, `payload`, `payload_ref`).
- DISABLED drops exactly `error.message` and `metadata.*` (VERIFIED against `blackbox/redaction.py:276-279`), nothing else: tags, `run.name`, `tool.operation`, `span.name` and other free-text labels (KI-078) stay. That is parity, and ADR-130 §3 says "exactly what the SDK's DISABLED drops", but the plan's "no free text such as error messages and run metadata" reads as broader. Suggest the plan's closing note states the exact list, and that the parity test enumerates every attribute key the SDK's `apply` treats specially (so a future SDK change to the DISABLED list fails the test).

---

## Areas reviewed with no substantive issue

- **Redaction ordering and channels.** Redaction runs after validation and before `PgEventStore.ingest`, the summary/outbox jobs, `run_search` and the stream (they all read stored rows). Attribute *keys* cannot carry secrets (`ATTRIBUTE_KEY_PATTERN` `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$`); payload dict keys are scanned (`redaction.py:261`); tags, string lists, nested lists in dicts, `payload_ref` (dropped outside FULL; the artifact body is scanned in FULL) and artifact names are covered. `_fit` truncation only removes scanned text. Required attributes become `[dropped]`, keeping the event valid; a list-typed required attribute would fail validation and be rejected (`REDACTION_FAILED`), which the SDK counts as rejected and does not retry (`exporter.py:229-238`): no infinite retry. `except Exception` paths in `_text`, `label`, `artifact_body` and `_redact_batch` all end in a drop or a rejection, never in storing the input. Client-supplied `redaction.*` is discarded. Logs carry counts and type names only (`"redaction crashed"`, `"artifact redacted"`). Exactly-at-limit budgets: `used > max` and `len > unit_limit` are strict, so a value exactly at the limit scans (VERIFIED). ReDoS: every quantifier is bounded or possessive; the 50-60 K hostile inputs took 1-12 ms; the 1.5 s / 8 s test bounds would catch a quadratic pattern (200 K characters squared is minutes).
- **Idempotency and modes.** `apply` is deterministic for a given mode (modulo P2-3); a redelivery under a changed mode is a `conflict`, first version wins, documented in ADR-130 §8 and tested (`test_a_redelivery_after_the_mode_changed_keeps_the_first_version`). Artifact retries compare the raw digest, then the FULL-mode redaction, then "dropped by id"; the claimed `X-Content-SHA256` is checked against the body as sent. Non-FULL artifact paths store only `placeholder_body`; no path writes the raw bytes (`stored = decision.body` is the only `put`). The default flip to METADATA_ONLY is deliberate, has no backfill by design, and is called out in the migration docstring, ADR-130 §9, the runbook ("The default changed") and `make seed`; the `.sh` scripts pass `--capture-mode full`.
- **Tenancy, authz, audit.** Every new repository statement carries `workspace_id` (settings store, security findings repository, security inputs, run_ids). Project resolution goes through `authorise_project` (project-bound keys confined; foreign and malformed ids are the same `PROJECT_NOT_FOUND`); settings routes resolve the project through the tenant-scoped `ProjectRepository.get` and `FOR NO KEY UPDATE` on the project row (`with_for_update(key_share=True)` renders that in PostgreSQL), so two admins' partial PUTs serialise and each audit row describes exactly its delta (tested: `test_concurrent_changes_are_applied_one_after_another`). No SQL is built from strings: filters are SQLAlchemy `in_` with query-pattern-validated inputs (`DetectorId`, `SeverityParam`, `RunStatusName`, `id_pattern`), cursors go through `cursors.decode(..., "security", width=2)`. Role matrix: GET/PUT settings are `project.write` (owner/admin, `NO_KEY`); list/detectors are `run.read` (BILLING 403); the route-walking registry and the OpenAPI count include all four routes. Audit rows are written only on a real change, in the same transaction as the row and the re-analysis jobs (`test_the_audit_row_and_the_jobs_commit_together`); no-ops write nothing (documented in the route description). Re-analysis queues only the project's own runs (`RunRepository.run_ids` is tenant- and project-scoped; `test_only_the_projects_own_runs_are_queued_and_authz_is_unchanged`).
- **Detectors and wording.** Every title, message, `rule` and description is a module constant in `security_words.py`; the only interpolation is `describe.py:243-254`, which joins `KIND_WORDS[k]` for kinds filtered through the closed `KIND_WORDS` dict. `rules.py` functions return booleans, enums and host strings that never leave the detector (`hosts_in_command` results are compared, not stored; `test_matchers_never_return_the_text`). Evidence keys are allowlisted per finding type (`model.py:261-265`). Counts and event ids reveal "which events", which the evidence-link design accepts (D2); event types are visible to every run reader already. Mute is applied at detection time (`SecurityDetector.detect`), so a muted detector leaves no rows, and the settings change re-queues analysis. `DETECTORS_VERSION` 2 marks every old analysis `outdated` in both badges; the runbook says to `rebuild-findings`. The R3 deviation is recorded in ADR-131 §3. The benign corpus contains the adversarial cases asked about (`cat .env.example`, `grep -r ... .env.example`, `git push --force-with-lease origin feature/x`, `rm -rf node_modules`, `rm -rf ./build`, `DELETE FROM sessions WHERE ...`, `chmod -R 755 ./bin`), and the wrapper stripping covers `sudo`, `env VAR=1`, `command`, `\rm` (backslash escape), `bash -c` to depth 3; evasions are listed in KI-132.
- **Web.** No `dangerouslySetInnerHTML`/`innerHTML` in the security components; every rendered string is a known field (`securityLine` ignores the rest of `evidence`). `?finding=` is accepted only if it matches an id in the run's findings with `category === "security"`; filter params are allowlisted (`parseSecurityFilters`), `since`/`until` must be tz-aware ISO. The proxy's PUT is allowed for exactly `projects/{id}/security-settings` (method checked against the matched routes, `405` otherwise, VERIFIED in `security-proxy.test.ts`), goes through the same exact-origin CSRF check as POST/PATCH/DELETE, requires `application/json`, caps the body, forwards only the session cookie; the fixtures PUT path is fixtures-only and in-memory. Settings page is gated by `SettingsPage needs="project.write"`; the rail link needs `run.read`. The shield glyph is `aria-hidden` with the word as the name; `security.css` reads `--wait` (amber) and ink/muted only, never `--fail`/`--bad`; the form shows what the API returned (`setQueryData` from the response), not optimistic state; `useSyncExternalStore` has a server snapshot (no hydration mismatch).
- **Tests and CI.** The modified existing tests are additions or fact-updates (`test_search_http` asserts the new fact and keeps a positive assertion; `ingest_helpers` keeps `None` for the product default and a test proves the default; `test_detectors` version bump; e2e run counts). Mutation reasoning: removing the scanner call fails `test_full_redacts_every_secret...`; removing the settings read fails `test_a_project_without_a_settings_row_is_metadata_only` and `test_metadata_only_stores_no_payload...`; removing workspace scoping in `SecurityFindingsRepository` fails the "another workspace sees nothing" cases in `test_security_findings_access.py`; putting content in a finding fails the sentinel assertions. `security-e2e.sh` uses generated local-only keys and sessions (never printed), the dev-only fake provider, a dedicated database, exports the planted (random, fake-shaped) values after the servers start, and scans its own logs for them; the CI job uploads logs only on failure and they are asserted sentinel-free. Ports 8190-8192/3190 are unique to this job.
- **Migrations and ops.** Single alembic head; 0090 sits after 0071 (the 0080 `run_search` revision is already in the chain before 0070). The runtime role gets DML on the new table through 0007's default privileges, and the tests run as a role inheriting `abb_runtime` (`conftest.py:96-112`, `test_the_runtime_role_can_read_and_write_settings`). Config defaults are sane and bounded (`le=5000`, `ge=1024`). `api-v1.md`, ADR-130/131, the runbook and `openapi.json` agree with the code on routes, fields and error codes; `RunOut.security_findings` and `FindingOut.category` are additive.

## Disposition suggested

Fix P1-1 and P2-2 in this phase (small, no contract change beyond the SDK pattern which is a bug fix). P2-1 either fix now with the SDK, or file as S2 with a dated target and say so in `docs/SECURITY.md` ("stored text may carry `[REDACTED:credential]` where an assignment looked secret-shaped"). P2-3 and P2-4 are a doc line and a one-method refactor. The P3 items can go to KNOWN_ISSUES with S3.
