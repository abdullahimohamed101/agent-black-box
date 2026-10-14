# Testing

Pyramid and commands. Commands below are real as of Phase 0 (`make test lint typecheck`, `scripts/quality.sh`); `make e2e` and load tests arrive later.

| Layer | Scope | Tooling |
| --- | --- | --- |
| Unit | event validation, redaction, pricing, policy matching, sequence logic, detectors, authz | pytest / vitest |
| Repository | real PostgreSQL: tenant scoping, dedup, cursors, transactions, migrations | pytest + Postgres (Compose or native) |
| Contract | SDK fixtures vs JSON Schema; adapter golden canonical-event fixtures; schema compat vs previous minor | pytest |
| Integration | SDK -> API -> DB -> worker -> query | pytest |
| Browser E2E | demo agent -> SDK -> API -> Postgres -> SSE -> UI; filters; drawers; reconnect | Playwright |
| Load | realistic payloads, bursty starts, long-lived streams; results to `docs/benchmarks/` | locust/k6 (decide in Phase 18 plan) |

What the Phase 2 suite looks like (all against real PostgreSQL, ~310 API tests):

- **Constraint tests** prove the database itself rejects cross-tenant references, duplicate events and bad enum values.
- **Store tests** cover idempotency, same-id-different-content conflicts, 50-way concurrent identical batches, overlapping batches
  (deterministic proof of stable write order by capturing the SQL), atomicity, and append-only code.
- **API tests** go through the real application: authentication matrix, body/gzip limits (including a memory-bounded zip bomb),
  partial rejection, rate limiting, post-auth failures, log hygiene, fuzzed batches, and the query API with tenancy matrices.
- **Equivalence tests** assert that run state equals a from-scratch derivation under random batching, ordering, duplication and
  worker timing; **order-sensitive** scenarios make sure ordering bugs cannot hide.
- **Worker tests** cover claiming (`SKIP LOCKED` with two concurrent claimers), leases, retries, dead letters, lost leases, and the
  real worker process (start, work, SIGTERM, exit 0).
- **OpenAPI tests** keep `openapi.json` current and its auth/error documentation honest.
- `make smoke` (containers) and `make bench` (latency baseline, `docs/benchmarks/`) are not part of `make test`.
- Every test has a 90 s timeout so a deadlock fails fast. Security-relevant logic is **mutation-checked** during review
  (change the code, confirm a test fails); tests that survive a mutant are strengthened, not kept.

### Web (Phase 4)
- **Vitest + Testing Library** (`apps/web/tests`): pure timeline/dashboard/format logic, the read proxy (allowlist, key never forwarded
  to the browser, upstream auth failure mapped to 502), the fixture API (cursor paging, filters), and components (run detail, runs list,
  dashboard, drawers) with every loading / empty / error / not-found state, keyboard navigation and hostile payloads rendered as text.
  The typed client stays honest through `pnpm --filter @abb/web gen:api:check` (part of `scripts/quality.sh`).
- **Fixtures** (`apps/web/src/fixtures`): success, failure+retry, expensive, running, awaiting approval and a 10,000-event stress run,
  deterministic and served by the same read routes when `ABB_WEB_DATA_SOURCE=fixtures`.
- **Playwright** (`make e2e`, port 3100, system Chrome so no browser download): fixture smoke, axe (WCAG A/AA, no serious/critical),
  focus return, a bounded DOM for 10,000 events, mobile overflow, screenshots to `docs/screenshots/phase-4/`.
  `make e2e-real` (`scripts/e2e-web-real.sh`) starts an API (:8110) and worker against a dedicated `abb_p4` database, ingests runs over
  HTTP and drives the UI through the web proxy (:3102). It never touches the shared dev/test databases.
- **Streaming E2E** (`make stream-e2e`, `scripts/stream-e2e.sh`, CI job `stream-e2e`): API (:8120) + worker on a dedicated `abb_p5`
  database, the built web server (:3103), Chrome via Playwright, and `scripts/stream_driver.py` writing runs (the Python SDK, or plain HTTP).
  Covers: the SDK example's trace appearing live with no page reload and ending cleanly, axe and console errors on a live page, a browser
  refresh mid-run (every event once), a connection cut by a fault-injecting TCP proxy (partial-data notice, recovery, no loss), and the
  accept-to-display latency gate (p95 < 1 s; recorded in `docs/benchmarks/phase-5-streaming.md`). Screenshots: `docs/screenshots/phase-5/`.
  Server-side streaming is tested over a real socket (`apps/api/tests/test_stream_api.py`), because an in-process ASGI transport buffers
  whole responses.

- **Multi-agent E2E** (`make multiagent-e2e`, `scripts/multiagent-e2e.sh`, CI job `multiagent-e2e`, Phase 9): dedicated `abb_p9` database, API (:8130)
  and worker, the built web server (:3104). `examples/multi-agent` (threads, an asyncio task, a real subprocess joined through `BLACKBOX_TRACE_CONTEXT`
  whose clock is 1.5 s behind, one failing tool, full payloads) writes a real run; the script then asserts the stored run (five agents, root agent
  `planner`, `ordering_mode: time`, the tester's span parented to the coder's, summary version 3) and runs `apps/web/e2e/multiagent.spec.ts` as an OWNER
  and as a VIEWER (two throwaway development sessions): waterfall nesting under skew, the skew badge, the critical path, lanes, the agent tree, the
  failed-tool jump, and that a VIEWER sees the same structure but never the tester's command (rows, tooltip, lanes, timeline, drawer). Example tests:
  `uv run --project packages/sdk-python pytest examples/multi-agent/tests`.

- **Evaluations E2E** (`make eval-e2e`, `scripts/eval-e2e.sh`, CI job `eval-e2e`, Phase 11): dedicated `abb_p11` database, API (:8180) with `ABB_JUDGE_PROVIDER=fake` and a worker, the built web server (:3180).
  The scripted coding agent runs twice through the SDK (`--variant baseline`, and `--variant wrong-fix`, which ends in `run.failed`); `scripts/eval_e2e_check.py` creates the evaluators (rule, metric, human, the fake
  judge, and a judge whose rubric leaves no room for the run data), evaluates both runs, records a verdict carrying hostile text, proves that a failed evaluation leaves the run row identical (status, summary, `updated_at`),
  creates an experiment with a baseline and a candidate that differ in two settings, and asserts the owner's and the viewer's comparison over the API. `apps/web/e2e/eval.spec.ts` then drives a real browser as an OWNER
  (every evaluation state and the "not ground truth" badge, a verdict recorded in the form, the compare page: divergence at the `file.read` that opens the second edit, the first outcome difference at the second test run,
  "Jump to divergence", keyboard; the experiment's not-controlled banner naming `model` and `variant`, "Evaluate all" filling the metrics) and as a VIEWER (scores and labels but no explanation text, a metadata-only
  comparison whose divergence is the second test run, no paths or commands in the page, no forms). axe on every page; screenshots in `docs/screenshots/phase-11/`. The fixtures Playwright spec covers the same pages
  against fixtures (compare, experiments, the viewer's views, mobile overflow). The compare fixture pair is generated from the real example by `scripts/gen_compare_fixture.py` (never hand-edited);
  `scripts/bench_compare.py` records the alignment benchmarks (`docs/benchmarks/phase-11-compare.md`).

### Visual replay (Phase 12)
- **Web unit**: `replay-projection.test.ts` (state at any index equals an independent recomputation over the fixture runs; incremental advance; checkpoints; the 10,000-event bound), `replay-pacing.test.ts` (clamping, speeds, gap labels,
  skewed run), `replay-hook.test.tsx` (player, URL, keyboard, pause at the first error), `replay-transport.test.tsx` (controls, slider, phone menu), `replay-run.test.tsx` (the run page wired to the playhead: header figures as of the playhead,
  a headline that does not leak the future, the timeline and fault line, inspector follow, waterfall spans and the agent highlight, a viewer's replay, live append, keyboard precedence, the URL) and `replay-waterfall-bounds.test.ts`.
- **Fixtures Playwright** (port 3100; `e2e/replay.ts` is the shared helper, expected values are computed from the fixture events inside the specs): `e2e/replay.spec.ts` (network isolation: every request a same-origin GET and none made by the page
  after replay starts, with a control for Chrome's own tab-icon refresh; the D5 success test at 1440 and 375 px; a viewer's replay; the keyboard map; `?t=` through reload; reduced motion; the 375 px bar; axe in both schemes; hydration),
  `e2e/replay-agents.spec.ts` (spans appear and grow, the agent highlight, the skew note), `e2e/replay-perf.spec.ts` (10,000-event scrub jumps and 5x playback responsiveness; the numbers are printed and attached as annotations),
  `e2e/replay-shots.spec.ts` (skipped unless `SHOTS_PHASE12=1`; writes `docs/screenshots/phase-12/`).
- **API-backed**: `e2e/replay-viewer-real.spec.ts` (a real viewer and owner through the auth E2E environment: the viewer's replay shows no content and asks for nothing of its own; the owner is the control; and on a run with
  no cost attribute and two edited files, both roles' replay ends on the header's own Cost and Files changed). `scripts/auth-e2e.sh` lists it in its Playwright command and ingests that run.
- **Review fixes (Phase 12)**: `replay-fidelity.test.ts` (a viewer's files changed, the settled cost and its floor/"about" wording, blocked status and headline), `replay-review.test.tsx` (the same on the page, Esc with a
  hidden inspector, live appends are incremental, `r` on an empty run, the playhead inside a collapsed group, speed changes and live arrivals mid-gap), `e2e/replay-fidelity.spec.ts` (owner and viewer, fixtures), and on the API
  `apps/api/tests/test_event_costs.py` (per-event cost equals the run summary, one lookup per page, workspace scoping, index plan) and `tests/authz/test_content_withheld.py` (identical for every role).

### UI shell and design pass (Phase 21)
- **Design contract** (`apps/web/e2e/design.spec.ts`, fixtures build, port 3100): frame geometry at 1440x900 (rail 224 px, top bar 44 px, `main` to the viewport edge), axe (WCAG A/AA, no serious or critical) on 11 routes in both colour
  schemes, 375 px with no horizontal scroll, the status grammar (one red and one amber element in a failed run's header, radius and shadow scans), the run work area (no document scroll, header at most 190 px, pane at least 480 px beside the
  inspector, 10,000-event DOM bound), the inspector (flush right, overlay on a phone), the Evidence view (including the viewer: no command text, no `[withheld]`), the runs list, fonts and no third-party request, and the motion layer:
  with `reducedMotion: "reduce"` `document.getAnimations().length === 0` after load, after opening the inspector and after a view switch; with motion on, nothing animates on first paint on any route, the inspector transition is 180 ms,
  a hovered row 120 ms and `0s` while the keyboard drives the list. The Phase 21 polish checks (one-row class toolbar at 1440 and 1280, one story marker, the folded context strip, clamped agents, tab counts) live here too.
- **Screenshots** (`apps/web/e2e/shots.spec.ts`): skipped unless `SHOTS_DIR=before|after` is set, so `test:e2e` never rewrites committed files. `SHOTS_DIR=after pnpm exec playwright test e2e/shots.spec.ts` regenerates the 21 pictures in
  `docs/screenshots/phase-21/after/` (1x scale, to match the committed `before/` set).
- **Web unit** additions: `tokens.test.ts` (every text role at least 4.5:1 on every background, both schemes), `app-shell.test.tsx`, `run-header.test.tsx`, `run-layout.test.tsx`, `runs-header.test.tsx`, `evidence.test.ts(x)`.
- Under heavy machine load (load average above 10) a few timing-sensitive tests flake without a code change: `waterfall-perf`, `coding.spec` "~220 KB log" (a `waitForTimeout(100)` race), `stream.spec` p95 latency (30 s test timeout), `findings.spec`. Re-run once; a flake that repeats is a bug.

### Auth, workspaces and RBAC (Phase 15)
- **Authorization generator** (`apps/api/tests/authz/`): `registry.py` lists every route the application serves with the expected outcome for each of
  27 actors (13 key kinds, six roles, and people who are not members, were removed, were demoted, hold an expired or revoked session, send a session as a
  bearer or a key as a cookie, or omit the workspace header). `test_registry_is_complete.py` walks `app.routes` (including HEAD/OPTIONS) and fails for a
  route without a case; `test_role_matrix.py` runs the table against a real database and socket; a literal copy of the matrix in the tests is compared
  with `authz/matrix.py` in both directions (`test_matrix_data.py`); `test_cross_workspace.py` scans every body and SSE frame for another workspace's
  canary values and compares analytics magnitudes; `test_no_scattered_checks.py` is an AST scan against role or scope inspection outside `authz/`.
- **Sign-in** (`tests/test_auth_sessions.py` with `tests/fake_oidc.py`, an in-process OIDC provider with knobs for every bad token, nonce, key, audience,
  issuer and unverified email): state/cookie binding, identity linking and conflict, discovery and JWKS rules, open redirect, CSRF, expiry, revocation,
  downgrade and removal taking effect at once, database-down 503, logout idempotence, log hygiene.
- **Administration**: `test_members_api.py` (last-owner rule under concurrency, owner changes, bounds, invitations), `test_api_keys_api.py`,
  `test_pricing_api.py`, `test_audit.py`/`test_audit_api.py` (one row per action, denial flood bounded, no secret in `details`),
  `test_payload_access.py`, `test_runtime_role.py` (append-only tables), `test_stream_reauth.py` (a revoked key or session, or a removed member, ends an
  open stream within the re-check interval), `test_cli_access.py` (gated `create-session`, dev owner, `relink-user`).
- **Web unit** (`apps/web/tests`): the proxy forwards exactly the header allowlist and no shared credential, rejects cross-origin writes, sign-in handlers
  relay `Location` and `Set-Cookie` without following redirects, session helper and gate, settings pages in every state.
- **Auth E2E** (`make auth-e2e`, `scripts/auth-e2e.sh`, CI job `auth-e2e`): dedicated `abb_p15` database, API (:8165, stream re-check every 2 s), the
  DEVELOPMENT-ONLY fake provider (:8166, `scripts/fake-oidc.sh`), a worker and the built web server (:3160). `apps/web/e2e/auth.spec.ts` drives a real
  browser: redirect to sign-in and login through the provider, an HttpOnly cookie, workspace switch and cross-workspace 404, owner sees a payload,
  invitation link and key token shown once, an invited person joins, role downgrade to VIEWER withholds payloads and hides settings, removing a person ends
  their live stream (`STREAM_UNAUTHORIZED`), sign-out; axe on the login and settings pages. Screenshots: `docs/screenshots/phase-15/` (committed).
- **The other E2E scripts no longer use a shared read key**: `e2e-web-real`, `coding-e2e`, `stream-e2e` and `analytics-e2e` mint a development session for a
  throwaway workspace owner (`create-session`, only with `ABB_ENVIRONMENT=development|test` and `ABB_ALLOW_DEV_SESSIONS=1`, which the scripts set for that one
  command) and pass it to Playwright as `E2E_SESSION_TOKEN` (`apps/web/e2e/session.ts` puts it in the browser as the HttpOnly cookie) with `E2E_WORKSPACE`.
  Specs that call the proxy directly add `X-ABB-Workspace` (`workspaceHeader`).

### Reliability findings (Phase 10)
- **Detectors** (`apps/api/tests/test_detectors.py`): the ten committed pathological runs (`tests/fixtures/pathological/*.ndjson`, generated by
  `scripts/pathological_runs.py`, `--check` proves they are current) against **hand-written** `*.expected.json` (exact set of findings, no extras): looping,
  identical requests, retry storm, context growth, two interleaved agents (only one loops), skewed clocks, cost spike, tool-failure cascade, failure chain, and a
  healthy run with ordinary growth, spaced retries and one tool failure that yields nothing. Also shuffle invariance, bounds (50 per detector, 200 per run, 4,096
  character labels, 64-item patterns, evidence under 15 KiB), hostile numbers, content canaries in every attribute a detector might copy, and a 50,000-event micro-benchmark.
- **Health** (`test_findings_health.py`): each dimension's boundaries, `no_data` in every state but `current`, no numeric score.
- **Storage and job** (`test_findings_repository.py`, `test_findings_job.py`): tenant scoping, upsert keeping `id` and `created_at`, the cost baseline's window
  and exclusions, equality of stored findings with a from-scratch run over randomly batched ingestion (cost spike excluded, tested with a frozen baseline), the
  capped load equals the canonical prefix even with ties, the enqueue delay clamp, two concurrent jobs, a detector raising a canary dead-letters only its own job
  and leaves no canary in `last_error` or the logs, `rebuild-findings` (bounded `--limit`, audited).
- **API** (`test_findings_api.py`, `tests/authz/test_findings_content.py`): the finding contract, evidence ids resolving, state walk including `outdated` and `failed`
  with the previous items, byte-identical bodies for OWNER, VIEWER and a `runs:read` key, BILLING 403, identical 404s, no canary for any actor; the route is in the authz registry.
- **Web unit** (`apps/web/tests/findings.test.tsx` and the extended run-detail and runs-list tests): every analysis state, probable/observed wording, the evidence jump, hostile strings rendered as text.
- **Findings E2E** (`make findings-e2e`, `scripts/findings-e2e.sh`, CI job `findings-e2e`): dedicated `abb_p10` database, API (:8135, detector delays 0), a worker and the built
  web server (:3105). The ten pathological runs go through the real ingestion path in shuffled batches; `scripts/findings_e2e_check.py` asserts each run's findings
  against the hand-written expectations, that every cited event resolves, that titles say "Probable:" exactly for probable findings, that a second person added as
  VIEWER (the only OWNER cannot be demoted) receives byte-identical items and health and no event content, and that two `rebuild-findings` runs reproduce the same ids,
  fingerprints, evidence and `created_at`. `apps/web/e2e/findings.spec.ts` drives a real browser as OWNER and VIEWER (loop, cascade and failure chain panels, "Show
  evidence" opening the cited event, the runs-list column, axe). Screenshots: `docs/screenshots/phase-10/`.

### Search (Phase 16)
- **Grammar and compiler without a database** (`apps/api/tests/search/test_parser.py`, `test_tokens.py`, `test_tsliteral.py`, `test_compiler.py`): every §152/§34 example, negation, limits and
  positions, a seeded 10,000-string fuzzer (only `QueryError`, under 10 ms), the tsvector/tsquery literal serialisers and a self-match property, and the compiled SQL text of a corpus of queries:
  for a principal without `payload.read` it never contains `content_doc`, `file_paths`, `commands` or `content_truncated`; every statement binds the workspace; user strings are bound parameters;
  `to_tsquery` never appears.
- **Postgres semantics** (`test_search_api.py`, `test_search_http.py`, `test_search_index.py`, `test_document.py`): the literal syntax pinned against the real server, every field, operator, negation, prefix,
  glob and phrase, null and unindexed behaviour, keyset paging of 120 runs while matches arrive, the hash-skipped upsert, `search-rebuild`, no total count and no relevance field.
- **Oracle and tenancy** (`test_search_oracle.py`, `tests/authz/test_content_withheld.py`, `test_cross_workspace.py`): a VIEWER and a downgraded owner get identical items, facets and cursor for a content canary in
  every syntactic form as for a nonsense word; `file:`/`command:` is `403` with the canonical field, no run statement executed, logged and audited without the query; BILLING gets `403` before parsing;
  no facet, `matched` or `content_truncated` leaks; the globex canaries are never found from acme.
- **Cost bounds** (`test_search_budget.py`, `test_search_hostile.py`): timeout and busy are retryable `503`s, a flood of slow searches leaves ingestion and the plain list at `202`/`200` and no search backend running,
  at most two statements per search, `q=` equals the plain statements, hostile strings are `422` or literal matches and never `500`.
- **Web unit** (`apps/web/tests/search.test.tsx`): chips from `query.terms`, removal by span, legacy URLs converted to `q`, caret on a 422, a 403 explained without echoing the value (never "no results"),
  414 like 422, busy/timeout with a retry, the unindexed notice, hostile strings rendered as text, a viewer's help listing only what the API allows; `upstream.test.ts` pins the 414 case.
- **Browser** (`make auth-e2e` also runs `apps/web/e2e/search.spec.ts`; the fixtures spec has a search test): an owner finds a run by command text and path; a VIEWER typing `file:` or `command:` gets the
  refusal and nothing else, sees the same page for captured text as for a nonsense word, searches metadata with chips, facets and keyboard only; axe on every state; no horizontal scroll at 375 px.
  Screenshots: `docs/screenshots/phase-16/`.
- **Mutation checks** are recorded in `docs/plans/active/phase-16-search-investigation.md` ("Implementation notes").
- **Benchmarks**: `scripts/bench_search.py` (latency on a seeded database) and the A/B ingest comparison in `docs/benchmarks/phase-16-regression-check.md`.

Rules: never skip/weaken a failing test; no mocks where a real Postgres test is feasible; UI
changes are exercised in a browser; fixtures are deterministic (fixed IDs/timestamps).

Commands (targets):
```bash
make test        # event-schema + api (real Postgres) + web unit
make schema-check # generated JSON Schema / TS types are current
make lint        # ruff, eslint, prettier --check
make typecheck   # mypy, tsc
scripts/quality.sh quick   # schema + openapi currency, ruff, mypy, eslint, prettier, tsc, pytest, vitest
scripts/quality.sh full    # quick + migration up/down/up on the TEST database + next build + wheel build
```
Database tests need Postgres (`make db`). `ABB_TEST_DATABASE_URL` names the server and credentials; the
test session creates and drops its own throwaway database (migrated to head), tables are truncated
between tests, and migration tests create one database each. Neither the dev database nor `abb_test`
holds test data.
```text
```

- **Coding-agent E2E** (`make coding-e2e`, `scripts/coding-e2e.sh`, CI job `coding-e2e`): dedicated `abb_p6` database, API (:8140) with a
  throwaway artifact directory and a worker; the scripted demo agent (`examples/coding-agent`) writes a real run through the SDK; a
  server-side scan (`scripts/coding_e2e_check.py`) searches events, `artifacts.name/kind/uri` and every artifact file for the planted
  secrets; Playwright (`apps/web/e2e/coding.spec.ts`, web on :3140) checks the story, diff, lazy shell output, chunked loading and that no
  planted secret reaches any page or API response. The script refuses to start if the port is already serving and kills the whole
  process tree on exit. **The Playwright spec writes screenshots into the tracked `docs/screenshots/phase-6/`**: commit them when they changed
  meaningfully, otherwise `git checkout` them. Example tests: `uv run --project packages/sdk-python pytest examples/coding-agent/tests`.

### Server-side redaction and capture modes (Phase 13 step 1, ADR-130)
- `apps/api/tests/test_security_scanner.py`: one sample per kind, near misses, unicode, NUL/escape splitting, idempotence, size and time budgets, and 22 hostile
  1 MB inputs with a wall-clock bound; `test_security_scanner_parity.py` loads the SDK from the monorepo and asserts the same kinds, deny keys, output on a corpus and
  per-mode decisions. `test_server_redactor.py`: limits that redaction could break (lengthened strings, 64 attributes, inline payload size), budgets, determinism.
- `test_ingestion_redaction.py`: through the real API, per mode and fail-closed, redelivery and mode changes, artifacts; each test scans every table
  (`tests/secret_scan_helpers.py`: `find_in_database`) and the artifact directory (`find_on_disk`) for the planted secrets. `test_security_settings.py`: defaults,
  workspace/project scoping, validation, audit atomicity, concurrent changes, runtime role. `tests/authz/test_content_withheld.py` covers the markers for every role.
- `make_tenant(..., capture_mode=CaptureMode.FULL)` is the default for test projects; pass `None` for the product default (METADATA_ONLY).
- Review fixes (Phase 13 step 5): `test_security_scanner.py` also holds the corpora that the scanner must leave alone (ordinary `-u` commands, benign assignments, long user names, non-ASCII text) and must still redact
  (`user:password` arguments, real secret assignments, look-alike characters, secrets straddling a window cut); `test_security_scanner_parity.py` runs those corpora and every attribute of the schema through the SDK and the server
  in each mode. `test_artifact_redaction_kinds.py` (repository method and the module boundary), `test_security_bench_bounds.py`, and the cooldown tests in `test_security_settings.py`. `make_settings` sets the re-analysis cooldown to 0
  for tests. A change to the SDK's redaction patterns can change recorded fixtures: regenerate `apps/api/tests/fixtures/compare_pair` with `scripts/gen_compare_fixture.py --out` (a test fails when it is stale).

### Security detectors and findings (Phase 13 step 2, ADR-131)
- `apps/api/tests/test_security_rules.py`: every matcher with positives, negatives and edges (secret-file names, destructive commands, risky git, SQL, hosts and the
  allowlist), plus 35 adversarial 200 KB inputs run through every matcher with a wall-clock bound (ReDoS). `test_security_detectors.py`: the six detectors through
  the real `run_all` (severities, rule wording, R3 vs R4, muting, allowlist wildcards, localhost, SDK markers, artifact records), sentinel tests (a planted
  string never reaches any finding field), the precision corpus (`tests/security_corpus.py`, about 245 benign commands and paths, must yield zero findings with and
  without an allowlist; add every false positive you find) over the pathological and comparison fixtures and the schema example events, and a 10,000-event
  timing test (`-s` prints the measured time).
- `tests/authz/test_security_findings_access.py`: a planted run through the real ingestion path and worker (AWS-style key, private key, `.env` read, `rm -rf /`,
  force push to main, `DROP TABLE`, off-allowlist host) yields exactly the expected findings with evidence ids and fixed messages, identical for owner, admin,
  developer, viewer, security and read keys; billing is 403; facets, filters, paging, search, cross-workspace and cross-project isolation, settings roles,
  validation without echo, audit rows, mutes and allowlist changes through `rebuild-findings`. `tests/test_security_findings_job.py`: artifacts, per-project
  settings, INV-2, and a 10,000-event run through ingestion, summary and detection. The route registry and role matrix list the four new routes.

### Security end to end (Phase 13 step 4, gates 1-7)
- **Security E2E** (`make security-e2e`, `scripts/security-e2e.sh`, CI job `security-e2e`): dedicated `abb_security_e2e` database (`SECURITY_E2E_DB_NAME`), API (:8190), a
  second API on :8192 with a nanosecond scan budget and a 2 KiB artifact cap (same database and artifact directory), a worker, the dev-only fake OIDC provider (:8191) and the
  built web server (:3190). Pieces: `scripts/security_e2e_plant.py` (the planted and the ordinary run through the REAL Python SDK and `CodingRecorder`; harmful commands are recorded
  as spans with the classifier's verdict, never executed; the SDK process gets a clean environment because the helpers mask host environment values),
  `scripts/security_e2e_check.py` (the API, database and disk assertions, one subcommand per gate), `scripts/security_bench.py` (gate 7), and
  `apps/web/e2e/security-real.spec.ts` (the browser, signed in through the real OIDC flow as an owner, a viewer and a billing person; axe in light and dark; screenshots to
  `docs/screenshots/phase-13/real-*.png`). Per gate: (1) exactly the six findings with evidence events resolved as the owner, fixed wording, no planted value, byte-identical bodies for
  owner, developer, viewer and a read key, billing 403; (2) raw HTTP "SDK that skips redaction" into a FULL project, then a scan of every text/jsonb/tsvector/bytea column of the
  database, the artifact directory and the server logs for the sentinels (with a positive control), the stored redaction record, the oversized payload (`too_large`) and, on the strict
  API, `scan_failed` and `too_large` for events and artifacts; (3) FULL / METADATA_ONLY / DISABLED projects get the same payload-bearing run and are checked in the database and
  through the API; (4) the ordinary SDK run has zero security findings; (6) findings deleted by SQL come back identical from `rebuild-findings`, and a mute, an unmute and an
  allowlist change update the findings in the background without a rebuild; (7) 100-event batch latency and a 10,000-event run with asserted bounds
  (`docs/benchmarks/phase-13-security.md`). `SECURITY_E2E_LOG_DIR` keeps the server logs of a failed run. The precision corpus of gate 4 is in the API suite
  (`tests/test_security_detectors.py`, `tests/security_corpus.py`).
- `apps/api/tests/authz/test_security_reanalysis.py`: a settings change queues recent runs (mute, unmute, allowlist), a capture mode change and a no-op do not, edits coalesce
  to one job per run, only the project's own runs are queued, refused changes queue nothing, the bound by count and by age, newest first, and 0 turns it off
  (KI-133). `tests/test_artifact_name_redaction.py`: a secret in an artifact name is redacted before storing.

### Policies and approvals (Phase 14, ADR-140/141/142)
- **API** (`apps/api/tests/policies/`, `tests/approvals/`, `tests/authz/`): the DSL parser and validator, the glob matcher and the evaluator (golden policies, property tests: ordered, first match wins,
  defaults, policy id and version on every decision), the versioned store and the seven management routes, the dry run; the decision endpoint, the approval state machine and its database guard
  (`test_state_machine_db.py` connects as the runtime role and tries illegal SQL), the intent hash against `packages/event-schema/tests/data/intent-hash-vectors.json`, consume (nine variants of a different intent, a second
  use, 50 at once), expiry, long polling and its caps, the flood limits, roles and self-approval, content (sentinels in every field, never in a response, event, audit row or log), the policy re-check at consume, the reserved event
  types, and a latency test (`docs/benchmarks/phase-14-decide.md`). The route registry, role matrix and withheld-content classification list the new routes, actions and attributes.
- **SDK** (`packages/sdk-python/tests/test_policy_*.py`, `policy_server.py` a threaded fake of the endpoints): `guard()` flows, fail modes, the cached table, bounds, intent limits and the golden vectors, the decorator, the
  coding helpers (a test executes `policies/mapping.py` and compares), robustness (a 120-draw exception fuzz per scenario, 32 threads, hostile arguments, 26 mutations of the defences), the https path. Adapters:
  `abb_conformance.check_guarded_tool`, the MCP tests; the demo: `examples/coding-agent/tests/test_policy_demo.py` against a fake server.
- **Web** (`apps/web/tests/{policies-settings,approvals-page,policy-run,policy-lib,policy-proxy,policy-fixtures,policy-css}*`, fixtures Playwright `approvals`, `policies`, `policy-run` specs): the Policies settings page (line and
  column messages, history, diff, dry run, conflict), the Approvals page and rail count, decisions on the run page, in light and dark, at 1440 and 375 px.

### Policy end to end (Phase 14 step 5, gates 1-8)
- **Policy E2E** (`make policy-e2e`, `scripts/policy-e2e.sh`, CI job `policy-e2e`): dedicated `abb_policy_e2e` database (`POLICY_E2E_DB_NAME`), API (:8200), a SECOND API process on :8202 (same database; the checks stop and restart it by the PID of the
  process group they started, never by name), a worker, the dev-only fake OIDC provider (:8201), the built web server (:3200), and a login role in `abb_runtime` for the SQL attacks. The world: a workspace with an owner, an admin, a developer, a
  viewer and a billing person, seven projects (`full` with the starter policy, `expiry`, `failmodes`, `races`, `ui`, `bench`, `other`) and agent keys **created by the owner through the management API** (so self-approval applies to the owner),
  plus a second workspace with its own admin and key. Pieces: `scripts/policy_e2e_check.py` + `policy_e2e_gates.py` + `policy_e2e_lib.py` (API, database and disk assertions, one subcommand per gate), `scripts/policy_e2e_sdk.py` (the real SDK against a live and a
  stopped API, a slow responder and a hung one), `apps/web/e2e/policy-real.spec.ts` (a real browser through the OIDC flow; axe in light and dark; screenshots to `docs/screenshots/phase-14/real-*.png`). `POLICY_E2E_STAGES=1,2` runs only some stages
  (a development aid; CI runs all) and `POLICY_E2E_LOG_DIR` keeps the server logs of a failed run (they hold no payload bodies, secrets or key tokens, which the final scan asserts).
  Per gate: (1) `run_demo.py --policy-demo` through the real SDK: the `.env` read is denied (recorded by the policy service, never read), the push to main pauses (run `WAITING_FOR_APPROVAL`, no commit in a local bare remote), the admin approves or denies in the browser
  after seeing the banner with the real time left, the demo proceeds (commit in the bare remote) or gets `ApprovalDenied`, and a one-minute approval that nobody decides expires into a denial; (2) consume with a different command, resource, argument, extra argument, risk class, agent, run and capability: nine
  `APPROVAL_INTENT_MISMATCH` and nothing spent, then once, then `APPROVAL_CONSUMED`; other key, other project, other workspace: `APPROVAL_NOT_FOUND`; before approval, denied, cancelled, expired, and after a deny rule was published: the fixed codes; a fresh ask after use is a new request; no intent value or hash in any
  agent response, audit row, event or log; (4) a live API then none: R0-R2 and unclassified open, R3/R4 closed, operator override, `on_error=`, the cached table, one `policy.warning` per fall-back once the API returns, timeouts against a slow and a hung responder, a wait that breaks, `on_expiry` deny and allow; (5) 20 concurrent decisions
  five times, 30 concurrent consumes, approve against cancel (both orders occur), approve against expiry, and 14 illegal statements as the runtime role; (6) the role matrix over the API, the browser as viewer, billing, developer, owner and admin, self-approval,
  planted secrets scrubbed in the approver's view and absent from events, audit and the database scan, policy edits audited; (7) SIGINT during a wait cancels the approval, the wait cap, only documented exceptions across all scenarios, the standard-library audit; (8) the dry run
  against an independent evaluation (a regular-expression glob and a hand-written event mapping) over 720 crafted events and the demo's own runs, 500 sequential and 1,000 concurrent decisions against a 60-rule policy and ingestion latency alone and
  under decision load (`docs/benchmarks/phase-14-policy-e2e.md`). The final scan searches every text/jsonb column, the artifact directory and the logs for the planted secrets and key tokens (and, in the logs, the intent values and hashes).

### Alerts end to end (Phase 17 step 4, gates 1-8)
- **Alerts E2E** (`make alerts-e2e`, `scripts/alerts-e2e.sh`, CI job `alerts-e2e`): a dedicated `abb_p17_alerts_e2e` database (`ALERTS_E2E_DB_NAME`; **dropped and recreated on every run**, because rules left by an earlier run would keep alerting
  to receivers that listen on the same ports), API (:8210), a worker, the dev-only fake OIDC provider (:8211), the built web server (:3210) and local receivers started by the script (`scripts/alerts_e2e_receivers.py`): a signed webhook receiver that
  verifies the HMAC and timestamp (:8213), a second webhook receiver that can really go down (:8216), a Slack-shaped receiver (:8214), an SMTP sink (:8215, STARTTLS off via `ABB_SMTP_STARTTLS=false`) and a control port (:8212: `/state`, `/mode`
  `ok|500|429|400|302|slow`, `/listener`, `/secret`). Destinations must be HTTPS, so the API and the worker run with the **test-only network file** (`ABB_ALERTS_TEST_NETWORK_FILE`, ADR-151 addendum): reserved `.test` names resolve from a JSON file
  to a stub public address (changeable at run time to simulate a rebinding name), connections go to 127.0.0.1, TLS trusts one generated self-signed certificate; everything else (address check, pinning, redirects, deadline) is production code.
  Limits are tuned so waits are short: backoff 1 s doubling to 8 s, 4 attempts, failing after 3 and paused after 6 consecutive failures, 40 deliveries a minute and 60 a day per destination, a 2 s request deadline, a 2 s sweep.
  Pieces: `scripts/alerts_e2e_check.py` + `alerts_e2e_lib.py` (one subcommand per gate; every assertion prints `PASS`/`FAIL` with the observed values and a final table is printed), `apps/web/e2e/alerts-real.spec.ts` (a real browser through the OIDC flow;
  axe; screenshots to `docs/screenshots/phase-17/real-*.png`). `ALERTS_E2E_STAGES=1,2` runs only some stages (a development aid; CI runs all) and `ALERTS_E2E_LOG_DIR` keeps the server logs and result lines of a failed run (no payload bodies, secrets or key tokens).
  Per gate: (1) five rules (approval requested, approval about to expire with a 60 s lead on a 2-minute approval, security finding at or above `medium`, run failed/timed out/blocked, spend over $0.01) each fire exactly once to a signed webhook, a Slack
  receiver and an SMTP sink, through the real API and worker; the security findings come from a run the **real Python SDK** plants (`scripts/security_e2e_plant.py`); the receivers' alert ids equal the history's; a successful run, a decided approval and a
  15-minute approval alert nothing more; (2) receiver down -> backoff -> delivered once with the same `alert_id` and a signature that verifies; a 500-ing receiver: exactly 4 requests then `dead`, auto-pause at 6 with a system audit row, resume; 30 alerts to one
  failing destination make 6 requests; a slow receiver is cut off at 2 s (`timeout`); (3) fourteen unsafe targets refused at save (HTTP, loopback, `localhost`, IPv6 loopback, IPv4-mapped, metadata, link-local, RFC 1918, internal names, decimal and hex spellings,
  credentials), a name public at save and `10.0.0.9`, `169.254.169.254` or `::1` at delivery refused with no connection made, a redirect never followed, send-test likewise; (4) a unique sentinel planted in a file path, shell command, host, tool argument and error
  text of every ingested run is in no webhook/Slack body, email, history row (owner, admin, developer, viewer, security), audit row, alert table, job payload or log, with positive controls; (5) a scan of every text/jsonb/bytea column of the whole database finds no
  destination URL token, URL, signing secret (including the one the browser was shown) or recipient; masked in the API, history, audit and logs; shown once; rotation gives a new secret once and the receiver, which really verifies, rejects the stale one;
  (6) role matrix over the API (14 verbs for each of developer, viewer, security, billing), history withholding destination ids and approval ids, API keys get 403, a second workspace gets 404 for every foreign id and cannot trigger or receive anything of ours, every
  kind of change is in the audit log; (7) re-sending a batch, a late event and a second terminal event re-summarise a run without a second alert; a cooldown, a 40-a-minute rate limit (burst of 60) and a 60-a-day cap (two bursts of 40, a minute apart) are
  recorded as skipped with their reasons; (8) ingestion batch latency with and without six enabled rules, interleaved (`docs/benchmarks/phase-17-alerts-e2e.md`). The browser creates a destination (a loopback target is refused on the form), sees the signing secret once
  (not in storage, URL or DOM after Done and a reload), sends a test the receiver verifies, creates a rule and sees its preview line, then after the script made an alert fire sees the delivered history row with a link to the run; axe and the console are clean.
