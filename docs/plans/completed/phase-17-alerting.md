# Phase 17: Alerting and integrations

Status: Completed (merged 2026-10-14, PR #171; steps 1-5 built and evidenced below, the adversarial review `phase-17-code-review.md` (no P0, P1 1, P2 6, P3 9) is fixed or dispositioned, see Progress step 5). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-13).
Owner: api + web. Branch: `feature/phase-17-alerting` (from `main` after PR #170).
Depends on: Phase 10/13 (findings, security findings), Phase 14 (approvals, expiry, `server_secrets` pattern), Phase 7 (cost rollups), Phase 15 (roles, audit), Phase 22 (UI grammar).
Spec refs: §35 (alerts), §72 (processors idempotent and retryable; dead-letter), §73 (tables incl. `alerts`), §62 invariants (INV-2, INV-3, INV-4 style bounds on every queue and retry, INV-5), §90 threat model.

## Outcome

A person who is not watching the dashboard is told when something needs them, through a destination they control (signed webhook, Slack, email), with a link back to the app. Alerts point at one checkable thing, carry no content, and cannot be used to probe the internal network.

## Non-goals

- No rate-based or anomaly alerts (failure-rate percentages, retry spikes, latency percentiles, version regressions): they need baselines and tuning.
- No native PagerDuty or Teams; no runtime plugin loading (an in-repo `Notifier` contract only).
- No in-app notification centre, per-user preferences, on-call schedules, escalation or acknowledgement workflow.
- No two-way Slack interactivity (for example approving from Slack).

## Decisions (settled with the user)

- D1 Triggers (four, all computed from what the product already stores, metadata only): (1) an approval is requested, and once more a set time before it expires; (2) a security finding at or above a chosen severity; (3) a run failed, timed out or was blocked, optionally filtered by project and agent; (4) spend over a threshold per project or agent per day, from the existing cost rollups, firing once per rule per day. Every alert points at one checkable thing (an approval, finding, run or day's spend) with a link.
- D2 Model: destinations and rules are separate objects. A destination is a named target of type webhook, Slack or email and holds the secret part (webhook URL and signing secret, Slack incoming-webhook URL, email recipient list). A rule has one trigger, its filters (project, optional agent, minimum severity or run status, or a daily spend threshold) and a list of destinations; destinations can be shared. Owners and admins create, edit, mute and delete, every change audit-logged; other roles with project access can read the rules list and history but never destinations or secrets. Destination secrets are encrypted at rest with a per-installation key (the Phase 14 `server_secrets` pattern), shown once, and only a masked form is ever returned or logged. Email needs operator SMTP configuration through environment variables (no mail credentials in the database); without it email destinations cannot be created and the UI says why. Webhook payloads are signed with an HMAC using a per-destination secret shown once. Per-rule noise control: a dedupe key (per finding, approval, run or day) and a cooldown.
- D3 Delivery: at least once with dedupe. Rule evaluation writes a notification row; a worker delivers it through the existing job framework; retries with backoff are bounded, then dead-letter with a fixed error code; receivers dedupe on a stable `alert_id`. Content-free alerts: fixed title, trigger kind, severity, counts, ids, project and agent names, time and a link to the app; never a path, command, host, argument, error text or anything the viewer rules treat as content (the link is where role rules apply). Outbound safety: HTTPS only; hostnames are resolved at delivery time and refused if they resolve to private, loopback, link-local, metadata or other non-public addresses (IPv4 and IPv6), and the connection goes to the resolved address (no DNS rebinding); no redirects; short timeouts (about 5 s); response bodies discarded after a small cap; per-destination rate limit and daily cap. A "Send test" action per destination. A delivery log for owners (time, rule, destination, status, attempts, fixed failure code, never a response body) with retention pruning. A destination that keeps failing is flagged, the owner is told in the app, and it is auto-paused after a bounded number of consecutive failures.
- D4 Evaluation is event-driven through the existing job system: the code paths that already produce these facts (a finding saved, an approval requested or changing state, a run reaching a terminal status) enqueue a small idempotent, debounced `evaluate_alerts` job that matches the event against the workspace's enabled rules and writes notification rows with a unique key per rule and event. Spend thresholds and approval-about-to-expire are scheduled jobs. The extension interface is an internal contract: a `Notifier` protocol (`send(destination, alert) -> result`) and a registry; webhook, Slack and email are built-in implementations; no third-party code is loaded at runtime. Rules only evaluate metadata the product already stores and never read event payloads.
- D5 UI: a workspace "Alerts" page in the rail (Phase 22 grammar) with tabs Rules, Destinations, History. Rules: a table (name, trigger, filters in words, destinations, enabled, last fired) and a plain form (trigger, then the filters it needs, destinations, cooldown, mute) with a preview line computed from recorded data ("would have fired 3 times in the last 7 days"). Destinations: masked target, type, health (OK, failing, paused), "Send test"; create shows the URL and signing secret once with a copy button. History: the delivery log with links to the run, finding or approval. A guided empty state ("add a destination, then a rule") with a starter. Owners and admins manage; other roles read rules and history; never destinations or secrets. The Approvals and run pages gain a small note when an alert was sent about that approval or finding. No new colours: severity uses the Phase 13 shields; failing destinations use amber; red stays for run failure.
- D6 Gates: (1) each trigger fires exactly once per event end to end (approval requested, approval about to expire, finding at or above the severity, failed/timed-out/blocked run, spend over the threshold) to a signed webhook receiver and a Slack-shaped receiver, and email to a local SMTP sink; (2) delivery is reliable (receiver down gets bounded retries with backoff and then the alert once it is back, deduped on `alert_id`; dead-letter after the cap; a failing destination auto-pauses; a retry storm is bounded); (3) outbound safety holds (private, loopback, link-local and metadata addresses, HTTP URLs, redirects and a hostname that rebinds to a private address are refused; IPv6 included; tested with a local receiver and a stub resolver); (4) alerts are content-free (a planted sentinel in a path, command, host, argument or error text never appears in any payload, email, history row, log or audit entry, for any role); (5) secrets are protected (a database scan finds no plaintext destination URL or signing secret; masked in API, UI, logs and audit; shown once; the HMAC verifies at the receiver); (6) authorization and tenancy (only owners and admins manage; others never see destinations; nothing crosses workspaces; every change audited); (7) noise control (dedupe and cooldown work; a flood of one event type does not flood a destination: rate limit and daily cap); (8) system cost (ingestion latency does not regress beyond a stated bound; evaluation reads only metadata).

## Known issues considered

KI-071 (no email delivery of invitations) stays out: this phase builds the email Notifier but invitation email is a separate change (the Notifier makes it cheap later). KI-090 (findings not in rollups) is untouched. KI-018/KI-019 (S1, Phase 19) are unaffected, but the new write endpoints are rate limited like the others and the outbound sender is bounded.

## Acceptance criteria

The eight gates of D6, each evidenced with a command and its observed output, plus: no existing test weakened (modified tests listed with reasons); authz matrix, route registry and withheld-content classification updated and green; `scripts/quality.sh full` green in CI; adversarial review with a security pass and no open P0/P1.

## Verification plan

Unit and property tests for rule matching, dedupe and cooldown; API tests for CRUD, authz, encryption, masking, audit; delivery tests against local receivers (signed webhook, Slack-shaped, SMTP sink) with scripted failures; SSRF tests with a stub resolver; sentinel no-content tests; web component tests and fixtures Playwright; an API-backed `alerts-e2e.sh` (real API, worker, receivers, browser) with a CI job; my own gate run; then a Fable review with a security pass.

## Risks

- The sender is an outbound-request cannon pointed where an owner says: SSRF defence, timeouts, caps and rate limits are part of the feature and tested adversarially.
- Secrets in the database: encryption with a per-installation key, never returned, never logged, never audited; rotation behaviour documented.
- Alert storms and retry storms: dedupe, cooldown, rate limits, daily caps, bounded retries, auto-pause.
- Evaluation hooks sit on hot paths (ingestion, summaries): they only enqueue; measured overhead.
- Overclaiming: alerts are best-effort notifications with at-least-once delivery, not a paging system with guarantees; docs say so.

## Ordered steps (one commit unit each)

1. Model and evaluation: tables and migration, secret encryption, destinations and rules CRUD API with authz and audit, notification rows with dedupe and cooldown, event hooks and `evaluate_alerts`, scheduled spend and approval-expiry checks, ADR.
2. Delivery: `Notifier` protocol and registry, SSRF-safe HTTP sender, webhook (HMAC), Slack, email (SMTP), retries, dead-letter, auto-pause, rate limits and caps, send-test endpoint, delivery log and pruning, ADR.
3. Web: Alerts page (Rules, Destinations, History), forms, preview, guided empty state, notes on Approvals and run pages.
4. Verification suite: `alerts-e2e.sh` with receivers and a CI job, SSRF and secrets scans, benchmarks, docs and closing evidence.
5. Review, fixes, push and PR.

## Progress

- Step 1 (model and evaluation, 2026-10-13): migration 0095 (`alert_destinations`, `alert_rules`, `alert_rule_destinations`, `alert_notifications`, CHECKs, unique dedupe key), `abb_api/alerts/`
  (`sealed.py` AES-256-GCM under a `server_secrets` key bound to row, workspace and purpose; `targets.py` early target guard and masking; `service.py`/`router.py`/`schemas.py` the 12 management routes;
  `evaluate.py`, `jobs.py`, `payload.py`, `subjects.py`, `spend.py` evaluation, the `evaluate_alerts` and `alert_sweep` outbox jobs and the content-free payload), producer hooks in
  `jobs/handlers.py` (`summarize_run`), `findings/job.py` and `approvals/service.py`, actions `alert.read`/`alert.write`/`alert.destination.read`, ADR-150, `cryptography` made a direct dependency,
  SMTP and alert settings, KI-160/161. Notification rows are written as `pending`; step 2 delivers them. Contract for step 2: `alert_notifications.payload_json` is the body (relative `link_path`, made
  absolute from `ABB_WEB_ORIGIN`; escape project, agent and rule names per channel); decrypt with `sealed.open_sealed(engine, blob, purpose="config"|"signing", workspace_id, destination_id)`;
  `alert_destinations.state/consecutive_failures/paused_reason` are the health fields; `DestinationRow` (repository.py) carries the ciphertext; `skipped` rows are never sent. Steps 2-5 open.
  Gate 8 evidence (step 1, `apps/api/tests/test_alerts_overhead.py -s`, 30 failing runs of 101 events, local PostgreSQL, idle machine): ingestion batch p50/p95 35.1/58.1 ms
  without rules and 33.1/36.6 ms with six enabled rules (the ingestion path does not touch alerting); worker time per run (summarise, findings, analytics and alert jobs) p50/p95
  142.8/160.3 ms without rules and 144.0/158.5 ms with them (a second run: 127.6/143.0 and 140.0/176.2). Verified (step 1): ruff check and format, `mypy`, full API suite 3198 passed
  (`tests/authz` 229 passed), `python -m abb_api.openapi --check`, `pnpm gen:api:check`, web typecheck/lint/prettier/vitest (1354 passed), `make schema-check`.

- Step 2 (delivery, 2026-10-14): migration 0096 (skip reasons `rate_limited`, `daily_cap`; delivered-count index), `abb_api/alerts/` `outbound.py` (SSRF-safe pinned HTTPS sender on `asyncio.open_connection(ip, ..., server_hostname=host)`, no new dependency),
  `notifiers.py` (`Notifier` protocol, registry, webhook HMAC, Slack, email), `delivery.py` (the tick: claim `FOR UPDATE SKIP LOCKED`, re-check, caps, send, retries with jittered backoff, dead letters, health and auto-pause with a system audit entry),
  `schedule.py`/`jobs.py` (`deliver_alerts`, `alert_prune`), `POST /v1/alerts/destinations/{id}/test`, ADR-151, KI-162..166. Decisions: excess over the rate limit or daily cap is skipped, not queued; a send test touches no health and writes no history;
  the operator hold is `ABB_ALERTS_DELIVERY_ENABLED=false` (tests default to it). Tests: `test_alerts_outbound.py` (SSRF matrix, pinning, redirects, timeouts), `test_alerts_notifiers.py`, `test_alerts_delivery.py`, `test_alerts_send_test.py`. Steps 3-5 open.

- Step 3 (web, 2026-10-14): the Alerts page (`/w/[workspace]/alerts`, rail entry for `alert.read`, `?tab=rules|destinations|history`), `AlertRules` (table in words, switch, form per trigger, saved-rule preview line, delete with confirmation), `AlertDestinations` (masked target, health, send test with fixed codes in words, pause/resume, rotate, create per type, `SecretOncePanel`: the secret is handed to the panel by a callback and removed from the mutation result, so it is never in the query cache, storage or URL), `AlertHistory` (filters, cursor, links), guided empty state, role gating (destinations never requested without `alert.destination.read`; a 403 shows the same explanation). Proxy allowlist for `/v1/alerts/*` (11 method/path pairs) with `tests/alerts-proxy.test.ts`; fixture API with in-memory writes (`src/fixtures/alerts.ts`); `ApiRequestError.retryAfter`. The send-test POST sends `{}` because the proxy requires a JSON content type on every POST. Not built: the note on the Approvals and run pages (the history endpoint has no `run_id`, `finding_id` or `approval_id` filter; filed as a KI row to file rather than scanning the log client-side) and renaming or re-targeting a destination (the API supports it; the page offers delete and re-create).

- Step 4 (verification suite, 2026-10-14): `scripts/alerts-e2e.sh` (`make alerts-e2e`, CI job `alerts-e2e`, needs `changes`, skipped on docs-only, logs uploaded on failure) with `alerts_e2e_check.py`, `alerts_e2e_lib.py`, `alerts_e2e_receivers.py` (a signed
  webhook receiver that verifies HMAC and timestamp, a second one that can really go down, a Slack-shaped receiver, an SMTP sink, a control port with `ok|500|429|400|302|slow` modes) and `apps/web/e2e/alerts-real.spec.ts` (OIDC sign-in, axe, console clean).
  The sender needed a way to reach local receivers without weakening it: `ABB_ALERTS_TEST_NETWORK_FILE` (`alerts/testnet.py`, ADR-151 addendum 1) replaces only DNS, the connect address and the trust anchor, is unset by default, is refused unless
  `ABB_ENVIRONMENT` is `development` or `test`, and `tests/test_alerts_testnet.py` proves both and that a private answer is still refused. 113 script checks (provision 3, gate 1 19, gate 2 10, gate 3 23, gate 4 15, gate 5 14, gate 6 22, gate 7 4, gate 8 1, browser 2 plus
  the two Playwright tests) pass in about four minutes. Observations: an approval decided within a second of being asked may or may not be reported as requested (the job reads the fact when it runs), so the suite leaves that one alert out of the exact-once
  sets and checks that a decided approval is never "about to expire"; the retry storm (30 alerts, one failing destination) made 6 requests, which is the auto-pause bound; one run in about twelve (before the suite recreated its database and stopped reusing
  keep-alive connections) saw no "about to expire" alert at all while spend alerts of the same sweep chain arrived (not reproduced in nine consecutive runs afterwards; see KI-160, no watchdog on the sweep chain).
  Closing evidence commands: `make alerts-e2e` (or `pnpm --filter @abb/web build && scripts/alerts-e2e.sh`); `cd apps/api && uv run --offline pytest tests/test_alerts_testnet.py tests/test_alerts_outbound.py tests/test_alerts_delivery.py tests/test_alerts_notifiers.py -q`;
  `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run --offline mypy && uv run python -m abb_api.openapi --check`; `pnpm --filter @abb/web lint && pnpm --filter @abb/web format:check && pnpm --filter @abb/web typecheck`;
  gate 8 numbers: `docs/benchmarks/phase-17-alerts-e2e.md`; operator runbook: `docs/runbooks/alerting.md`. Steps 5 (review, fixes, push, PR) open.

- Step 5 (adversarial review fixes, 2026-10-14): P1-1 a failed run is announced only on the transition into the status and never when it ended before the retention window (a pruned dedupe row plus a cost rebuild
  re-alerted every old run); P2-1 owner writes fail fast with `409 ALERT_BUSY` (`SET LOCAL lock_timeout`, 3 s) and `SKIP LOCKED` closing, one email send bounded by one SMTP timeout; P2-2 default batch 5 and the worker keeps
  the leases of jobs queued behind a slow one; P2-3 a requested approval is announced whatever state it reached (`details.approval_state`) and is back in the e2e exact-once set; P2-4 alert jobs re-arm the sweep and the
  sweep queues its successor last (a deadlock with a rule edit, reproduced and fixed); P2-5 destination health applied in id order (deadlock between overlapping ticks, reproduced and fixed); P2-6 wording; P3-1 `::/96`,
  P3-2 midnight, P3-3 expiring batch anti-join, P3-4 retention >= 7 days, P3-5 one write token per send test, P3-8 mutation `gcTime: 0`, P3-9 provisioning fails loudly; P3-6 and P3-7 noted in the ADRs. KI-167..171 filed;
  KI-160/162/163/166 updated. Tests: `test_alerts_review_fixes.py`, `test_alerts_sweep_chain.py`, additions to `test_alerts_evaluation.py`, `test_outbox_worker.py`, `test_alerts_targets.py`, `test_alerts_outbound.py`, `tests/alerts-page.test.tsx`.

## Closing evidence (2026-10-14, final tree)

**VERIFIED** = a command was run here and its output observed; the numbers below are the ones recorded at merge. Not re-run for this close-out (a docs-only change).

| Gate | Evidence (stage of `scripts/alerts-e2e.sh`, `make alerts-e2e`) | Status |
| --- | --- | --- |
| 1. Each trigger fires exactly once, end to end | stage 1 (`gate1`, 20 checks): approval requested, approval about to expire, security finding (planted by the real Python SDK), failed/timed-out/blocked run and spend over the threshold, each once to a signed webhook, a Slack-shaped receiver and an SMTP sink, through the real API and worker; receiver alert ids equal the history's; a successful run, a decided approval and a far-off approval alert nothing more. Detail: `docs/TESTING.md` "Alerts end to end". | VERIFIED |
| 2. Delivery is reliable | stage 2 (`gate2`, 10 checks): receiver down, backoff, delivered once with the same `alert_id`; a 500-ing receiver gets 4 requests then `dead`, auto-pause with a system audit row, resume; 30 alerts to one failing destination make 6 requests; a slow receiver is cut off at the deadline. | VERIFIED |
| 3. Outbound safety | stage 3 (`gate3`, 23 checks): fourteen unsafe targets refused at save; a name that is public at save and private, metadata or `::1` at delivery refused with no connection made; a redirect never followed; send-test likewise. Unit level: `tests/test_alerts_outbound.py`, `tests/test_alerts_targets.py`, `tests/test_alerts_testnet.py` (the test-only network file cannot weaken the sender). | VERIFIED |
| 4. Alerts are content-free | stage 4 (`gate4`, 15 checks): a planted sentinel in a path, command, host, argument and error text is in no payload, email, history row (five roles), audit row, alert table, job payload or log, with positive controls. | VERIFIED |
| 5. Secrets are protected | stage 5 (`gate5`, 14 checks): a scan of every text, jsonb and bytea column finds no destination URL, signing secret or recipient; masked in API, history, audit and logs; shown once; rotation gives a new secret once and the verifying receiver rejects the stale one. | VERIFIED |
| 6. Authorization and tenancy | stage 6 (`gate6`, 22 checks): role matrix over the API, history withholds destination and approval ids, API keys get 403, a second workspace gets 404 for every foreign id, every kind of change is in the audit log; `tests/authz` and the route registry updated and green. | VERIFIED |
| 7. Noise control | stage 7 (`gate7`, 4 checks): re-sent batch, late event and second terminal event re-summarise without a second alert; cooldown, rate limit and daily cap recorded as skipped with their reasons. | VERIFIED |
| 8. System cost | stage 8 (`gate8`, 1 check, bound asserted) and `docs/benchmarks/phase-17-alerts-e2e.md`: three interleaved runs on a loaded laptop, six enabled rules versus muted, median batch -16 %, +5 %, +24 % and p95 -7 %, +2 %, +21 %, every run inside the stated bound; the step-1 harness `tests/test_alerts_overhead.py` measured p50/p95 35.1/58.1 ms off and 33.1/36.6 ms on. A regression baseline on one laptop, not a capacity claim. | VERIFIED |

- `make alerts-e2e`: **PASSED** (provision 3, gate1 20, gate2 10, gate3 23, gate4 15, gate5 14, gate6 22, gate7 4, gate8 1, ui 2, suite 1), including the real-browser Playwright spec `apps/web/e2e/alerts-real.spec.ts` (OIDC sign-in, secret shown once, axe and console clean).
- API: full suite 3,390 passed and 1 failed; the failure is the pre-existing flaky `tests/test_runs_api.py::test_a_dead_lettered_summary_is_reported_as_failed_until_it_recovers` (KI-172, also seen on `main`), not an alerting test. Migrations 0095 and 0096; ADR-150 (model, evaluation) and ADR-151 (delivery) with amendments from the review.
- Web: `tsc`, eslint and prettier clean; vitest 84 files, 1,399 tests; fixtures Playwright 322 passed, 97 skipped (the API-backed specs, which `alerts-e2e` and the other `*-e2e` scripts cover).
- Known issues filed from this phase: KI-160..KI-172 (issues #172-#184).
- Not verified here: CI results are not reproduced in this document; the Approvals and run page notes were not built (KI-168); a deployment with real SMTP, Slack or a public receiver (only local receivers and a stub resolver, by design).
