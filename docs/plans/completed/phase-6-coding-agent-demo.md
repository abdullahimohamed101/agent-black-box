# Phase 6 - Coding-agent observability and the flagship demo

Status: Completed 2026-10-08 (not pushed; the `coding-e2e` CI job has not run on GitHub; compose stack and real-model mode UNVERIFIED (env))
Owner: implementer agent
Branch: `feature/phase-6-coding-agent-demo` (from `main` cc56935; worktree `abb-worktrees/phase-6`, runs in parallel with Phases 7 and 8)
Depends on: Phases 2 (ingestion, query), 3 (SDK), 4-5 (web run detail, live stream)
Spec refs: §26, §27, §75 (artifacts), §82 (82.1-82.3), §83 (risk classes), §136 (demo), §62 (INV-1/2/3/4/5/7/8); ADR-013, 020, 021, 022; new ADR-030, 031, 032

## Outcome
Run `examples/coding-agent` (an intentionally broken OAuth session-expiry repository, a real agent loop, a deterministic scripted model) and open the run in
the web app: the story *read -> model -> edit -> test fails -> retry -> edit -> tests pass* is readable without narration, diffs are syntax highlighted,
every shell command shows cwd, duration, exit code, risk class and lazily loaded stdout/stderr, test results are semantic (passed/failed/failing ids), and secrets
that the demo plants in its output are redacted in storage and in the UI.

## Non-goals
Enforcement of command risk (observe-only, spec §83), server-side secret scanning (Phase 13, KI-042), object storage (Phase 18, KI-041), quotas/retention (Phase 19, KI-040),
policy findings such as "agent changed authentication code" as stored findings (Phase 10/13; the UI shows an informational path label only), run comparison (Phase 12),
run-summary changes (the UI derives coding figures from events; `SUMMARY_VERSION` stays), LangGraph/OpenAI wrappers (Phase 8), web login (Phase 15).

## Current architecture (what exists)
- Registry already has `file.*`, `git.*`, `shell.command.*` (+ `shell.exit_code/cwd/risk_class`, `test.*`, `file.*` attributes); `payload_ref` is an `artifact://` string, unused.
- Skeleton `artifacts` table (0005; FK to `runs`), scope `artifacts:write`, id kind `art`. No store, endpoint or UI.
- SDK (stdlib only): `Run.event()`, spans, `Redactor` (deny keys + secret patterns + callback; payloads only in `PayloadMode.FULL`), bounded exporter thread.
- Web: `EventDrawer` renders model/tool sections and payload JSON as text; read proxy allowlists `/v1/runs*`; run summary has `files_modified`.
- E2E pattern: `scripts/stream-e2e.sh` (own DB, API, worker, built web server) + Playwright `e2e/stream.spec.ts`.

## Known issues considered
- KI-032 (S3, SDK secret detection best-effort): **pulled in.** Terminal output is the highest-risk captured text, so the same patterns are applied to whole texts and a
  value masker for the host's secret-looking environment values is added (ADR-031). Server-side detection stays Phase 13 (new KI-042).
- KI-029 (S1, one shared web key): stays. Artifact content flows through the same proxy and key, so the "never expose the web app publicly" warning matters more; stated in ADR-030 and `docs/SECURITY.md` is not edited (coordinator).
- KI-018 (S1, unbounded cardinality): stays, but artifacts add a storage growth axis; bounded per artifact now, quota recorded as new KI-040. KI-019 (S1): unaffected. KI-021/026/017: stay (artifact bodies are capped at 8 MiB and counted by the per-project limiter).
- KI-016/022 (large-run summarization), KI-027/028, KI-033/035: unrelated.
- New: KI-040 (quota/retention/orphans), KI-041 (single-node store), KI-042 (no server scan), KI-043 (regex highlighter), KI-044 (real-model mode unexercised).

## Decisions
- **D1 (ADR-030)** `ArtifactStore` contract + local FS; client-id `PUT /v1/artifacts/{id}`; hash check; idempotent; chunked JSON reads; events reference artifacts by attribute; FK to runs dropped.
- **D2 (ADR-031)** No new event types: optional attributes; tests are attributes on the closing `shell.command.*` event; risk R0-R4 + spec category from a deterministic SDK classifier; three-layer secret-safe capture.
- **D3 (ADR-032)** Highlighting is a local tokenizer rendered as React text; no dependency.
- **D4 SDK surface.** `BlackBox.upload_artifact(...)` (non-blocking, own bounded queue and thread, flushed with `flush/shutdown`; no-op unless `PayloadMode.FULL`) and `blackbox.coding`
  (classifier, `CodingRecorder` with `read_file/write_file/delete_file/git/run_command`, test-summary parsers). Kept in the SDK, not the example, because every coding-agent integration needs the same safe capture.
- **D5 Demo repository** uses `unittest` only (no installs) and a local bare Git remote created in a temp directory, so `git push` is real and offline. The scripted model's edits are
  computed from the actual file contents; if the repository drifts the script fails loudly instead of editing blindly.
- **D6 Real-model mode** is a stdlib HTTP client for the Anthropic Messages API behind `--model anthropic`; it reads the key from the user's environment, is never imported in scripted mode, and is never required (KI-044).
- **D7 Web** derives an *attempts* summary (each test run, retries between them, files changed) from the loaded events (pure functions, unit tested) and adds drawer panels for file, git and shell events.

## Proposed design
**API** (`abb_api/artifacts/`: `store.py`, `repository.py`, `service.py`, `router.py`, `schemas.py`): settings `ABB_ARTIFACT_DIR`, `ABB_ARTIFACT_MAX_BYTES`, `ABB_ARTIFACT_CHUNK_BYTES`. Migration `0030` adds
`project_id`, `name`, `media_type`, the project foreign key and `ix_artifacts_run`, and drops the run foreign key. `tables.py` updated in the same commit (the drift test enforces it).
**Schema** (`packages/event-schema`): attributes listed in ADR-031 added to `KNOWN_ATTRIBUTES`; regenerated JSON Schema/TS; valid examples for the new shapes; `docs/architecture/events.md`.
**SDK**: `redaction.Redactor.redact_text`, `artifacts.py` (uploader), `coding.py`.
**Web**: proxy allowlist; `lib/highlight.ts`, `lib/diff.ts`, `lib/coding.ts` (attempts, risk labels, sensitive-path labels); components `DiffView`, `ShellPanel`, `ArtifactText` (lazy chunks), `CodingSummary`; drawer sections for file/git/shell; timeline descriptions.
**Example**: `examples/coding-agent/` (`repo_template/`, `coding_agent/{agent,tools,models,scripted,anthropic_model}.py`, `run_demo.py`, `README.md`, tests).
**E2E**: `scripts/coding-e2e.sh` (database `abb_p6`, API :8140, web :3140), `apps/web/e2e/coding.spec.ts`, `make coding-e2e`, CI job.

## Affected files (conflict-prone with parallel phases: `scripts/quality.sh`, `Makefile`, `.github/workflows/ci.yml`, `apps/api/openapi.json`, `apps/web/src/lib/api/schema.d.ts`, `docs/DECISIONS.md`, `docs/KNOWN_ISSUES.md`)
New/changed per the design above, plus `docker-compose.yml` (artifact volume and env), `.env.example` (`ABB_ARTIFACT_DIR`).

## Acceptance criteria (each is evidenced with a command and its output in the completion table)
- A1 Artifact upload: stored, SHA-256 verified, idempotent on retry, conflict on different content, size cap 413, wrong scope 403, project-bound, cross-workspace read 404 (pytest on real Postgres).
- A2 Chunked read: offset/limit, UTF-8 boundaries preserved, `next_offset` null at the end, big artifact never read whole by one request (pytest).
- A3 Migration 0030: empty -> head, head -> base -> head, and 0009 -> 0030 with the drift test green.
- A4 Schema: new attributes validate and reject wrong types; generated artifacts current (`make schema-check`); SDK contract tests green.
- A5 Classifier: table-driven R0-R4 and category tests incl. compound, sudo, pipe-to-shell, SQL, force push, user override.
- A6 Secret-safe capture: planted secrets of every pattern kind and an env value of unknown shape are absent from the uploaded bytes; the child process cannot see the host's secrets; env never appears in any event (pytest).
- A7 Test-result parsing from real `unittest` and `pytest` output captured from the demo.
- A8 UI unit tests: diff parse/render, highlighter safety (no markup from trace content), shell panel lazy loading (no content request before expansion; second chunk on demand), attempts derivation, retry/error markers.
- A9 Demo scripted run end to end: `examples/coding-agent` produces the expected event sequence (read, llm, edit, test fail, retry, edit, test pass, commit, push) deterministically (pytest + the E2E).
- A10 Browser E2E through the real API, worker and built web server (`make coding-e2e`): the story is visible, diff is highlighted, shell panel lazy-loads the large output, planted secrets are absent from the page, from the API responses, from the artifact files on disk and from the database event rows; screenshots saved to `docs/screenshots/phase-6/`.
- A11 OpenAPI and the generated web client are current (`make openapi-check`, `pnpm --filter @abb/web gen:api:check`).
- A12 `scripts/quality.sh full` exits 0; security review pass recorded; mutation checks on the redaction and tenant-scoping tests.

## Verification plan
Focused tests per step, then `scripts/quality.sh full`; E2E by `scripts/coding-e2e.sh`; independent `verify-change` then `review-change` (security pass required: capture, upload, classes, XSS in the highlighter), then `harden-change`; mutation-test committed security code
(remove a redaction pattern, drop the project scope, skip the hash check, render raw) and confirm tests fail.

## Risks
- Secrets leaking through captured text (mitigated by layers; residual KI-042). Stored XSS through diff/terminal text (text-only rendering, test). Path traversal in the store (keys from validated UUIDs only; test). Disk exhaustion (per-artifact cap, rate limit; KI-040).
- The scripted demo drifting from the template (a pytest runs it on every CI run). Timing flakiness in E2E (wait on conditions, not sleeps).
- Parallel phases touching shared files (merge conflicts listed above).
- Docker compose cannot be re-verified without restarting shared containers (the compose change is validated with `docker compose config` only: UNVERIFIED (env) for a running stack).

## Ordered steps (one commit each)
1. Plan, ADR-030/031/032, KI-040..044 (docs).
2. Event-schema: attributes, examples, generated artifacts, docs.
3. API: migration 0030, `ArtifactStore`, upload and read endpoints, settings, tests, OpenAPI.
4. SDK: `redact_text`, artifact uploader, `upload_artifact`, tests.
5. SDK: `blackbox.coding` (classifier, runner, parsers, recorder), tests.
6. Web: proxy allowlist, generated client, highlighter, diff, artifact text, shell panel, drawer sections, coding summary, tests.
7. Example: repository template, agent, scripted model, optional real model, tests.
8. E2E: script, Playwright spec, Makefile, CI, screenshots.
9. Verify, review, harden fixes; completion evidence; move plan to `completed/`.

## Completion evidence (2026-10-08)

| # | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Upload: hash, idempotency, conflict, caps, scopes, project/tenant isolation | PASS | `apps/api/tests/test_artifacts_api.py` (real Postgres, runtime role): in the 427-test `uv run pytest` of `scripts/quality.sh full` (exit 0) |
| A2 | Chunked reads, UTF-8 boundaries, offsets | PASS | same file: chunk coverage test, offset/limit validation, HTML returned as JSON data |
| A3 | Migration 0030 | PASS | `quality.sh full` migration up/down/up on the test DB; `test_migrations.py` (each revision up/down/up, 0009 -> 0030, drift test) |
| A4 | Schema attributes, generated artifacts | PASS | event-schema pytest 346 passed; `make schema-check` clean; SDK contract tests green |
| A5 | Classifier R0-R4 | PASS | `packages/sdk-python/tests/test_coding.py` (55 table cases + override); sdk pytest 228 passed, 94.8% coverage |
| A6 | Secret-safe capture | PASS | `test_coding.py` (env never reaches child or events, masker, redaction in uploaded bytes, default mode uploads nothing); E2E server scan: 592 events and 11 artifact files, zero planted literals |
| A7 | Test-result parsing | PASS | unittest and pytest parser tests; demo events carry `test.failed=1` then `0` |
| A8 | UI unit tests | PASS | `pnpm --filter @abb/web test`: 303 passed (highlighter, diff, hostile content rendered as text, lazy shell panel, story, proxy allowlist) |
| A9 | Scripted demo event sequence | PASS | `examples/coding-agent/tests` 8 passed (run in `quality.sh`) |
| A10 | Browser E2E, real API | PASS | `make coding-e2e` exit 0: 6/6 Playwright tests (story, diff, shell panel lazy load, 220 KB log in 4 chunks, no secret in any API response or page, axe + console), screenshots in `docs/screenshots/phase-6/` |
| A11 | OpenAPI and web client current | PASS | `quality.sh full` steps `openapi.json is current`, `gen:api:check` |
| A12 | `quality.sh full` exit 0; security review; mutation checks | PASS | `quality=0` (log: "quality (full): OK"); review below; 22 mutations, all killed |

Mutation checks (committed code, restored with `git checkout <file>`), all killed by tests: SDK redaction (AWS pattern, scan precheck), env-value masker, allowlisted child environment, sensitive-path diff withholding, DROP DATABASE risk class, workspace path escape, payload-mode gate; API hash check, hash-equality conflict, project conflict, project scoping, workspace scoping, store root check, key validation, UTF-8 alignment, gzip-bomb cap, content-type allowlist; web artifact-ref parser, proxy allowlist, raw HTML rendering in `DiffView` and `ArtifactText`.

Security review (own diff): path and key traversal (keys from validated hex only, root containment, tested); size and gzip bombs (streaming cap on compressed and decompressed bytes); content type allowlist and JSON-only reads (no document serving); tenant and project scoping on every read, id collisions across workspaces isolated; 409 and idempotency; workspace-wide key rejected before the body is read (fixed during review); redaction before hashing and upload, hash is of stored bytes; API key masked in captured output; shell-span behaviour change documented in ADR-031. Residual: no server-side scan (KI-042), no quotas (KI-040), shell commands run as the agent user by design (observe-only classes).

Deviations: `docs/KNOWN_ISSUES.md` unchanged beyond KI-040..044. A pre-existing test (`test_stream_hub.py::test_stopping_closes_the_listener_connection`) counted listener connections server-wide and failed while another worktree's API shared the Postgres; it is now scoped to `current_database()`. `actionlint` is not installed; `.github/workflows/ci.yml` parses (PyYAML) with the new `coding-e2e` job.

## Review fixes (independent review, 2026-10-08)

Three P1 secret leaks and the P2/P3 items were fixed with regression tests (one commit per logical fix):

| Finding | Fix | Regression tests |
| --- | --- | --- |
| P1-1 artifact `name` carried the raw command | output artifacts are named `stdout`/`stderr`; diff names are sanitized; `upload_artifact` redacts any name itself; server names are at most 128 printable characters; `scripts/coding_e2e_check.py` scans `artifacts.name/kind/uri` | `test_artifact_names_are_fixed_or_redacted`, `test_the_sdk_redacts_an_artifact_name_itself`, `test_artifact_names_are_short_and_printable` |
| P1-2 `shell.command` and derived attributes only pattern-redacted, cut before redaction | everything derived from the command, cwd and names goes through `sanitize` (ANSI removed, env values masked, patterns) BEFORE the 256-character cut; new patterns for Authorization/Basic/Bearer, `-u user:pass`, `--password/--token` flags, `NAME_SECRET=`/`TOKEN=`/`KEY=` assignments, URL credentials; letters-only keys; ANSI-split tokens | `test_the_command_cwd_and_every_attribute_derived_from_them_are_redacted`, `test_a_secret_straddling_the_256_character_cut_leaves_no_prefix`, 16 pattern cases and the ordinary-text case in `test_redaction.py` |
| P1-3 `git_diff` uploaded a tracked `.env` | pathspec excludes plus hunk filtering for any diff-shaped output; commands naming a secret file have their output withheld (`shell.output_withheld`); changed-file counts skip secret files | `test_git_diff_never_includes_the_diff_of_a_tracked_env_file`, `test_generic_git_commands_strip_sensitive_hunks`, `test_commands_naming_a_secret_file_are_withheld_entirely` |
| P2 diff env masking, pre-check | `_attach_diff` sanitizes; pre-check keeps any 16-letter run (cost: 0.5 s for 4 MiB of prose, 0.03 s for the 220 KB demo log) | `test_diffs_are_masked_for_env_values_and_ansi_split_tokens` |
| P2 classifier | redirections, newlines, groups, control flow, wrappers, eval, `-lc`, interpreter code, awk, git global options and flag clusters; unparseable is R2+ | 51 dangerous and 9 harmless parametrized cases in `test_coding.py`; gaps documented in ADR-031 |
| P2 real-model mode | `--i-understand-this-runs-commands` required, README warning, no `HOME` in child env, flag-like git names refused | `test_real_model_mode_needs_the_explicit_opt_in`, `test_git_tools_refuse_flag_like_names`, `test_the_child_has_no_home...` |
| P2 doc drift | ADR-031 states the real truncation behaviour and exactly what is and is not covered | n/a |
| P3 | conflict responses do not reveal other projects; run/project mismatch 409; vanished file is 404 not 503; artifacts have their own rate bucket; `ABB_ARTIFACT_DIR` set in compose; file mode kept; DiffView caps O(n); more secret env names and 6-character minimum for clearly secret names; read_file redacts for the model; `coding-e2e.sh` refuses a busy port and kills process trees; TESTING.md notes the tracked screenshots | `test_artifacts_api.py` additions, `test_coding.py` additions |

Mutation checks on the new committed code (all killed, files restored with `git checkout`): each new redaction pattern (authorization, Basic, `-u`, flags, URL token user, secret assignment), the letters-only pre-check, ANSI stripping, name redaction in the SDK, `sanitize`, command/cwd/`test.suite`/`timeout.operation` sanitizing, fixed artifact names, sensitive-path withholding, hunk filtering, git pathspec excludes, changed-file filtering, diff sanitizing, `read_file` redaction, git ref validation, file mode preservation, HOME removal, unparseable-is-R2, redirection detection, newline splitting, git global options, `-lc` clusters, `eval`, group/lead-word unwrapping, nesting depth.

## Second review round (value masking, 2026-10-08)

A second re-verification (about 190 commands against a capturing server) showed variants still leaking, so the design changed from blocking command shapes to masking
secret *values* (ADR-031, "Design after the second review"). Commits: secret scanner and value masker, shell-aware path check, diff-section filter; 56 leak regression
cases; new redaction patterns, pre-cut redaction, C1 controls in names; classifier default inverted with 100+ new cases.

| Requirement | Result | Evidence |
| --- | --- | --- |
| Value masking from sensitive files, however the command read them (cat, `bash -c`, `python -c`, `grep -r`, `find -exec`, tar, `git show HEAD:.env`, ...) | PASS | `tests/test_secret_leaks.py`: 56 command shapes x (artifact bodies, names, events, model-facing output); encoded forms; commands that create secret files |
| Sensitive names case-insensitive and extended; value extraction (dotenv, JSON, YAML/TOML, PEM, `.pgpass`, `.htpasswd`, `.git-credentials`, `.npmrc`, `.netrc`) | PASS | `test_sensitive_names_case_insensitive`, `test_value_extraction`, scanner bounds test |
| Diff header shapes (`--git`, `--cc`, `--combined`, no/custom prefix, renames, plain, coloured; ANSI before filter) | PASS | `test_diff_section_filter_handles_every_header_shape` |
| New redaction patterns | PASS | `test_more_secret_shapes` (28 cases) and the non-redaction guard `test_names_that_merely_contain_pat_or_pass_are_left_alone` |
| Redact before every cut; C1 controls in names | PASS | straddling-cut tests (command capture, `read_file`), SDK and server C1 tests |
| Classifier default inverted; listed mislabels | PASS | `test_second_round_classifier_mislabels` (about 115 cases), read-only allowlist and known test/build tests |

Mutation checks on the new guards (all killed; each file restored with `git checkout <file>`): file-value masking, rescanning before and after each command, withholding, redact-then-cut,
capture slack, `read_file` slack, sensitive-path diff withholding, `.netrc`/PEM/JSON/base64/URL-encoded/git-history extraction, dynamic-content and glob detection, recursive readers,
inline-code bodies, `sh -c` recursion, `git grep|archive|cat-file`, ANSI-before-filter, diff-section header parsing, `REV:path` words, case-insensitive names, each new redaction
pattern (quoted keys, db/ssh/docker passwords, npm config, signed URLs, cookies, short env names, empty-user URL credentials, npm token, Slack webhook), classifier default, allowlist write flags,
`git -c` program config, `curl -X DELETE`, `python -m pip`, dynamic `eval`, disk tools.
