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
- **API-backed**: `e2e/replay-viewer-real.spec.ts` (a real viewer and owner through the auth E2E environment: the viewer's replay shows no content and asks for nothing of its own; the owner is the control). `scripts/auth-e2e.sh` lists its spec files, so
  the new file has to be appended to its Playwright command (see the Phase 12 plan, closing evidence).

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
