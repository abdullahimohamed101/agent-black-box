# Independent security review of the Phase 10 implementation (reliability intelligence)

Reviewed: branch `feature/phase-10-reliability-intelligence` at 4efcb63 (11 commits on main c212058), worktree
`../abb-worktrees/phase-10`, 2026-10-11. Stance: adversarial, against the implemented code and tests, not the plan's
description of them. No production code, test, fixture or script was changed: every mutation below was reverted with
`git checkout -- <file>` before the next one, the one throwaway probe file (`apps/api/tests/_probe_review.py`) was
deleted, and `git status` was clean except this file when it was written. Tests ran against the suite's own scratch
databases (`abb_test_<hex>` on the local Postgres); no other database was touched; `.env` was neither read nor printed.

Severity: **P0** critical, **P1** must fix before merge, **P2** should fix (or file and accept explicitly), **P3** optional.
Evidence vocabulary: **VERIFIED** (a command was run here, or the exact lines were read), **UNVERIFIED** (could not be
exercised here), **ASSUMED** (reasoned).

Summary: no P0. **One P1**: the exposed `fingerprint` (and the `id` derived from it) of an `identical_request` finding is
an offline guessing oracle on withheld payloads, because every other input of the hash is public; the plan's security
scenario 2 ("no actor can supply a candidate payload to compare against it") is false and the plan review's P2-3 was
moved one hash layer, not closed. **Three P2**: the event-load and row-read half of the detect job is outside the
`DetectorError` wrapper (a failure there quotes its cause into `outbox_jobs.last_error` and the dead-letter log);
ADR-081 and the code disagree on the severity of a long loop in a successful run (the code says `critical`, the ADR says
`info`); the evidence shrink loop in `validate_evidence` is unreachable dead code that no test exercises (a surviving
mutation). Six P3. Everything else held: 12 of 13 mutations were killed by an existing test, every P1 from the plan
review landed in code, tenancy is correct on every new query (including a project-bound key, which the implementer did
not test and I probed), the five test suites and the generator check pass here, and no content canary reached any actor.

---

## 1. Scope and method

- Read: `AGENTS.md` (INV-1..8), the plan with its dispositions, implementation notes and closing evidence,
  `phase-10-plan-review.md`, ADR-012/060/061/062/080/081, the Phase 15 security review (rules: `payload.read`, viewers see
  metadata only, 404-not-403, `CONTENT_ATTRIBUTES`, KI-078), `docs/benchmarks/phase-10-regression-check.md`, KI-090..096,
  the sibling plans and reviews in `../phase-9`, `../phase-11`, `../phase-16` (read only).
- Read in full: `findings/{model,build,job,repository,service,describe,tokens,health,router,schemas}.py`,
  `findings/detectors/*`, `jobs/{handlers,context}.py`, the diffs of `jobs/{outbox,worker}.py`, `ingestion/store.py`,
  `runs/{queries,service,schemas}.py`, `core/config.py`, `cli.py`, `db/tables.py`, `main.py`, migration `0060`,
  `event-schema/ids.py` and `ordering.py`, `registry.py` (`CONTENT_ATTRIBUTES`, `lookup`), `event.py` (type validation),
  `db/event_rows.py`; the tests `test_detectors.py`, `test_findings_{job,api,repository,health}.py`,
  `authz/test_findings_content.py`, `authz/test_content_withheld.py` (`REVIEWED_METADATA`), `authz/world.py`, the
  `test_summarizer.py`/`conftest.py`/registry/openapi diffs, `detector_helpers.py`, the ten `*.expected.json`;
  `scripts/pathological_runs.py` (check mode), `scripts/findings_e2e_check.py`; web `lib/findings.ts`,
  `components/Findings.tsx`, the diffs of `RunDetail.tsx`, `RunsTable.tsx`, `lib/queries.ts`, `server/upstream.ts`.
- Ran (VERIFIED): `tests/test_detectors.py` (67 passed, 2.2 s); `test_findings_repository.py` + `test_findings_health.py` +
  `test_findings_api.py` + `authz/test_findings_content.py` (81 passed, 13 s); `test_findings_job.py` without the
  60,000-event test (25 passed, 11 s) and that test alone (1 passed, 28 s); `tests/authz` (101 passed, 168 s);
  `pathological_runs.py --check` ("10 fixtures are current", exit 0); vitest `findings`, `runs-list`, `upstream`
  (42 passed); 13 mutation checks (section 5); 3 throwaway probes (section 4).
- Not run here (UNVERIFIED): `scripts/findings-e2e.sh` and Playwright (ports reserved by the parallel worktrees; the
  implementer's AC-12 evidence is taken as their claim), `make bench` on the compose stack (KI-096 states the gap),
  `scripts/quality.sh full`, migration up/down/up (the migration tests are in `tests/test_migrations.py`, not re-run).

## 2. Plan-review P1 items: did each fix land in code?

| Review id | Landed where (VERIFIED by reading) |
| --- | --- |
| P1-1 `EXPECTED_V1_OPERATIONS` 31 -> 32 | `tests/authz/test_registry_is_complete.py:12`; `test_openapi.py:55` `seen == 37`; the `RouteCase` at `tests/authz/registry.py:224-227`; the authz suite passes (101) |
| P1-2 evidence size caps | `model.py:25-31` (bounds), `:268-270` (`label`, 128 chars), `:244-253` (`LIST_LIMITS`), `:336-377` (`validate_evidence`); `test_maximum_size_client_strings_cannot_make_evidence_too_big`, `test_a_run_never_cites_more_than_fifty_events_per_finding`. See P2-3 for the part of it that is dead |
| P1-3 context growth as a rate | `detectors/context.py:73-98` (mean increment, factor, super-linear or absolute size); `healthy` fixture has 8k -> 24k over 8 calls and yields nothing (`test_the_healthy_run_yields_nothing...`) |
| P1-4 `DetectorError` fixed text `from None` | `model.py:60-67`, `detectors/__init__.py:54-57` (`run_all`), `job.py:115-119` (the assembly wrapper); killed by M3 |
| P1-5 health `no_data` unless current | `health.py:134-137`, `:96-102`; killed by M4 and M13 |
| P1-6 events-then-row snapshot | `job.py:80-92`: row (mode only), events capped at `limit + 1`, row again for cost and versions; status and counters folded from the events in `build.py:23-62` (`_status_after` imported from `runs/summary.py`, which is untouched); `test_status_and_counters_come_from_the_events_not_from_a_lagging_run_row` |
| P2-3 digest oracle | **Not closed**: the digest is out of the evidence but inside the fingerprint's key (`identical.py:63`). See P1-1 below |
| P2-4 fingerprint collision | `run_all` collapses by fingerprint (`detectors/__init__.py:58-61`) and `replace_run` again (`repository.py:104-109`) |
| P2-9 stable ids, `created_at` kept | `model.py:386-388` (UUIDv5), `repository.py:135-159` (`ON CONFLICT ... DO UPDATE` without `id`/`created_at`); `test_rewriting_keeps_ids_and_created_at_and_moves_updated_at` |
| P2-11 stale rows after a dead letter | `service.py:79-86` (`failed` precedes `none`), rows still returned; `test_after_a_dead_letter_the_previous_analysis_is_still_returned_as_failed` |
| P3-2 try-lock + re-enqueue | `job.py:63-78` (`pg_try_advisory_xact_lock`, key includes the workspace), `handlers.py:104-112`; killed by M2 |

## 3. Verified correct

- **Tenancy (INV-3).** Every new statement carries `workspace_id`: `FindingsRepository.replace_run` (`repository.py:162-167`
  for the stale delete, the rows carry `ws`), `for_run` (`:199-215`), `badges` (`:246-249`); `RunQueries.findings_inputs`
  (`queries.py:107`), `job_states` (`:167`; the dedupe key also embeds the workspace), `cost_baseline` (`:207`, killed by
  M1). The advisory lock key is `findings:{workspace_id}:{run_id}` (`job.py:67`). The route goes through
  `RunService.visible_run` (`service.py:96`), which applies the principal's project scope (`runs/service.py:320-332`), so a
  **project-bound key reading a run of another project gets the same 404 as a random id** and an empty runs list without
  badges: VERIFIED by probe 2 (section 4). Foreign (globex), random and malformed ids are identical 404s
  (`test_unknown_foreign_and_malformed_run_ids_are_identical_404s`). A detect job for a run of another workspace is a
  no-op (`test_a_job_for_an_unknown_run_or_another_workspace_is_a_no_op`). BILLING gets 403, an `events:write`-only key
  gets `INSUFFICIENT_SCOPE` (tests in `test_findings_api.py`), HEAD/OPTIONS through the role matrix (authz suite).
- **Content (INV-5, ADR-061).** Evidence is an allowlist per finding type with nested allowlists (`model.py:157-241`); a key
  outside it raises (`:355-356`), never silently drops. The values that reach evidence are ids, slugs, counts, event
  types, `tool.name`, `llm.model`, `tool.error_type`/`llm.error_type`, `shell.exit_code`, `http.status_code`, `loop.pattern`
  tokens and thresholds: all in `REVIEWED_METADATA` (`test_content_withheld.py:29-41`, KI-078) and already shown to
  viewers by the events route. `event_type` is free text for unknown-but-well-formed types (`event.py:275-276` accepts
  them) and appears in `pattern`, `chain` and `failures`, but it is constrained to `[a-z0-9_.]` x 64 (`registry.py:15`) and
  the events list shows it to every reader, so nothing new is exposed. Titles and messages are built only from evidence
  (`describe.py`). The canary tests cover every content attribute, `run.name`, tags, `test.suite`, `retry.reason`,
  `policy.reason`, payloads and a `custom.*` `thing.command` for OWNER, VIEWER, a project key and a workspace key, and the
  bodies are byte-identical (`test_findings_carry_no_captured_content_and_every_reader_gets_the_same_body`,
  `authz/test_findings_content.py`). M6 (a detector copying `shell.command` into an allowed key) is killed.
- **INV-1/INV-2.** The job writes only `findings` and `run_findings` (`repository.py:4-5`); events are untouched. Findings are
  a function of the loaded event set, the row's cost and the baseline (`job.py:1-7`); the INV-2 equivalence test runs the
  pathological runs through random batches and compares with a from-scratch `run_all` minus `cost_spike`
  (`test_findings_equal_a_from_scratch_run_over_the_complete_event_set`); a rebuild reproduces ids and `created_at`.
- **Concurrency.** `pg_try_advisory_xact_lock` is per workspace-run; the loser re-enqueues coalesced with a delay of at least
  1 s instead of holding a worker slot (`handlers.py:104-112`); `test_two_detect_jobs_for_one_run_never_both_write...` and
  `test_a_losing_job_that_is_not_pending_enqueues_a_delayed_successor`. The findings INSERT takes only `FOR KEY SHARE` on
  the run row through the composite FK, compatible with the summarizer's `FOR NO KEY UPDATE`; the busy path inserts only an
  outbox row; no lock cycle with the summarizer exists (ASSUMED from the lock matrix; the concurrency tests pass). The
  worker lease is 60 s with a heartbeat (`worker.py:97`); the worst measured job is 1.33 s (KI-096), so the lease has
  45x headroom. Settings reach the handler through a context variable set and reset per job (`jobs/context.py`,
  `worker.py:98,111`).
- **Bounds.** 50 per detector, 200 per run, lowest severity dropped first (`detectors/__init__.py:69-77`), a 1 MiB
  per-run evidence budget (`:78-84`), 48 ids per `event_ids` list so start and end fit in 50 (`model.py:246`), labels at
  128 characters, 20 pattern tokens, `evidence_event_ids` validated against the loaded events (`model.py:375-376`), the
  database CHECKs as a backstop (`0060_findings.py`: kind, severity, confidence, object, 16 KiB, cardinality 50, text
  sizes, fingerprint length 64). `ABB_FINDINGS_MAX_EVENTS` is `ge=100`; the capped load uses the full canonical key of the
  mode in SQL (`store.py:221-225`, `coalesce(sequence, 0)` matches `ordering.py:33-37`'s `sequence or 0`); the tie test and
  the 60,000-event test pass; M10 is killed. The delay clamp is `min(base x max(1, n / 10,000), max(base, 60))`
  (`job.py:56-60`). `rebuild-findings --limit` is argparse-bounded 1..10,000 (`cli.py:89-99`) and audited
  (`cli.py:588`). Thresholds are settings with `ge`/`gt` validators and a cascade window >= failures validator
  (`config.py:69-84, 135-141`).
- **Hostile attributes.** `attr_int` rejects bools, floats, NaN and strings; `attr_number` rejects non-finite values
  (`tokens.py:26-38`); negative token counts are skipped (`context.py:53`); a string `shell.exit_code` becomes `None`; a
  64-item `loop.pattern` is cut to 20 labels (`reported.py:25-30`); cyclic span ancestry ends the walk (`chain.py:67-75`);
  `test_hostile_attribute_values_are_skipped_not_raised_on` and `test_a_cyclic_span_ancestry_does_not_hang_the_failure_chain`.
  A detector that raises or breaks the evidence contract becomes a fixed-message `DetectorError` with no `__cause__` and a
  suppressed context; the canary is absent from `last_error`, the traceback and `caplog`
  (`test_a_detector_that_raises_dead_letters_only_its_own_job_without_leaking`); `summary_state` stays `current`.
- **Order independence and partitioning.** Every detector consumes `ctx.events` (sorted once by `sort_events`) and
  partitions by `agent_id` (`tokens.py:46-51, 88-94`); the shuffle test covers all ten fixtures; `two_agents` reports only
  agent A's loop; `skewed` judges bursts per emitter on `occurred_at`.
- **Health.** No numeric score (`test_there_is_no_numeric_score`); `reliability` and the ratio parts never depend on the
  findings state (`health.py:52-84`, `test_reliability_never_depends_on_the_findings_state`); `overall` ranks `no_data`
  below `ok` (`health.py:18`, M13 killed). The service builds the health facts from the run row's summary, never from the
  findings (`service.py:33-51`), so a failed analysis cannot make a failed run read healthy.
- **State and badge.** `processing > failed > none > outdated > current` (`service.py:79-86`); the badge is one
  `run_findings` query per page (`runs/service.py:212,230`) and derives `none|current|outdated` from `detectors_version`
  only (`:73-80`); `create_run` leaves it null. M7 (`outdated` read as `current`) is killed.
- **Cross-plan (merge second, after Phase 9).** `runs/summary.py` is untouched in the diff (`SUMMARY_VERSION` stays 2;
  Phase 9's security review line 46 confirms Phase 10 does not depend on the value). `_enqueue_detect` is the last
  statement of the `if events:` block (`handlers.py:77`) and `DETECT_FINDINGS` the last `HANDLERS` entry (`:118`).
  `RunOut.findings` sits immediately after `updated_at` (`runs/schemas.py:59`). Route counts 37/32. Migration chain
  `0047 -> 0060` (`0060_findings.py:19`), additive. `IdKind.FINDING = "fnd"` is one appended line. `upstream.ts` gains one
  alternation token. `job_states` is a new sibling with `summary_states` delegating (`queries.py:153-157`), as the review
  asked; Phase 11's plan still names `summary_states(job_type)` and should be told the method is `job_states`. The
  cost-spike baseline keys on `runs.agent_slug`, which Phase 9's D4 makes stable (root agent from the first `run.started`);
  Phase 9's note "Phase 10 must not treat `agent_slug` as authenticated" is honoured: the slug only selects a baseline and
  ADR-080 (4) states that an `event.write` insider can move it (a false or missing probable finding, not exposure).
  `RunFacts.agent_slug` (folded as `ordered[0].agent_id`) is not read by any detector (grep), so Phase 9's root-agent rule
  does not change detector output.
- **Tests.** `test_summarizer.py` now expects two follow-up jobs after the debounced summary (analytics refresh plus
  findings) instead of one: a strengthened count, not a weakened assertion; `make_settings` defaults
  `findings_delay_seconds=0` like the summary debounce. The `*.expected.json` files name events by `sequence`, carry the
  scenario text and arithmetic (for `cost_spike`: "nearest-rank p95 = the 24th of 25 = $0.03"), and the fixture test
  compares the exact multiset (`test_detectors.py:92-115`), so a tautological file would still have to be consistent with
  the rule table; the values I checked (`looping`: 11 repetitions -> `critical`, confidence `min(0.95, 0.5 + 0.55)`; the
  single-token stretch of 9 -> `warning` on a successful run) follow from the plan's rules, not from the code's quirks.
  Provenance beyond that cannot be verified. The generator check passes.
- **UI.** Every evidence string is rendered as React text (chips `Findings.tsx:90-98`, steps `:109-126`, inputs `:20-24`);
  `lib/findings.ts` type-checks every value before use; the state line covers none/processing/outdated/failed/current/
  truncated and says "Severity may change until the run ends" while active; `kindLabel` puts "Probable"/"Observed" in
  text; a cited event that is not loaded is fetched through `useEventDetail` and a failed fetch shows a dismissible
  notice (`RunDetail.tsx:191-200, 356-369`); `jumpTo` is a new function that `jumpToError` calls. 42 vitest tests pass.
  Browser behaviour: UNVERIFIED here (screenshots committed by the implementer).
- **KI-096 (1.33 s for 30,000 events, above the 1 s trigger).** Deferring is acceptable: the job is off the ingest path,
  coalesced to one run per 15 s at that size (about 9% of one worker's time for a single such active run), bounded by
  `ABB_FINDINGS_MAX_EVENTS`, and the lease has 45x headroom; the KI states the trigger and the knob. The compose-stack half
  of AC-11 remains UNVERIFIED (env), as the plan records.

## 4. Findings

### P1-1. The `identical_request` fingerprint (and its derived `id`) is an offline equality oracle on withheld payloads

Location: `findings/detectors/identical.py:63` (`key=f"{agent}\x1f{event_type}\x1f{name}\x1f{digest.hex()}"`),
`findings/model.py:380-388` (`fingerprint`, `finding_uuid`), `findings/service.py:57,72` (both exposed to every actor with
`run.read`), plan "Security review note" scenario 2 and ADR-080 (3).

Problem. The fingerprint is `sha256(detector | type | start_event_id | agent | event_type | tool_name | sha256(payload))`.
Every input except the payload digest is in the same response (`start_event_id`, `evidence.agent_id`,
`evidence.event_type`, `evidence.tool_name`) and the canonicalisation (`json.dumps(sort_keys=True, separators=(",",":"))`)
is public code. A VIEWER, or anyone with a `runs:read` key, who can guess a payload computes the fingerprint offline
and compares. "A hash of a hash" does not help: the attack is guessing, not inversion. This is exactly the oracle the plan
review's P2-3 described, with one more SHA-256 in the way; the plan's disposition table says P2-3 was accepted and the
closing evidence for AC-2 claims scenario 2 holds ("no actor can supply a candidate payload to compare against it"),
which is false. `id` is UUIDv5 over the fingerprint, so it leaks the same bit.

Reproduction (VERIFIED, probe 1, deleted): four `tool.call.started` events of tool `read_file` with payload
`{"path": "/etc/passwd"}` produce one finding; from the public fields alone,
`sha256("identical-request\x1fidentical_request\x1f<start_event_id>\x1f<agent>\x1ftool.call.started\x1fread_file\x1f<sha256 hex of the canonical payload>")`
equals the finding's `fingerprint`; a different candidate (`/etc/shadow`) does not. One SHA-256 per guess; common tool
payloads (a file path, a shell command, a known prompt) are enumerable. The existing tests cannot see it: they assert the
digest's hex does not appear (`test_detectors.py:566`, `test_findings_api.py:305-306`), which is true.

Fix. Drop the digest from the key: `key=f"{agent}\x1f{event_type}\x1f{name}"`. `start_event_id` (the group's first event)
is already part of the fingerprint and is unique per group, so identity across rebuilds is unchanged in every case the
plan cares about (a late-arriving earlier event moves it either way, ADR-080 (2)). VERIFIED test-neutral: with that
one-line change `tests/test_detectors.py` (67) and the API canary test pass (mutation M8). Alternatively HMAC the digest
with a server-side secret, which is more code for no gain. Add a test that computes the fingerprint from the public
fields plus the known payload and asserts inequality, and correct ADR-080 (3) and the plan's scenario 2 text. Bump
nothing: `DETECTORS_VERSION` need not move for a fingerprint change before merge, but stored rows in the E2E database
would differ (none exist on `main`).

**Resolution** (e88285a): the digest is out of the fingerprint key; regression test `test_the_identical_request_identity_is_independent_of_payload_content` (fails with the digest back in the key). Every other detector's key was checked and is public metadata; plan scenario 2, ADR-080 (3), SECURITY.md and the runbook corrected.

### P2-1. The event load and the row reads of the detect job are outside the fixed-message wrapper

Location: `findings/job.py:80-92` (`findings_inputs` twice, `PgEventStore.load_run`) versus the `try` at `:95-119`;
`db/event_rows.py:45-46` (`row_to_event` "validated again: the database is not trusted"); `jobs/worker.py:136`
(`last_error = f"{type(exc).__name__}: {exc}"`) and the dead-letter log with `exc_info`.

Problem. The plan's security scenario 7 promises "no ... attribute value, exception text or message in any log line or in
`outbox_jobs.last_error`" for this job, and the implementer flagged write and load failures as unwrapped. `load_run`
re-validates every stored event through the pydantic model; a validation failure's message quotes the offending input
(`input_value=...`). Today ingestion and `load_run` use the same model, so a stored event re-validates; the failure needs
a schema or registry change that tightens a limit or adds a required attribute to an existing type (`event.py:280-285`)
after events were stored, a maintenance scenario the summarizer already shares (pre-existing). The findings job was
supposed to be the one place that cannot leak.

Reproduction (VERIFIED, probe 3, deleted): with `abb_api.ingestion.store.row_to_event` raising
`ValueError("bad row CMDCANARY-...")`, the detect job dead-letters with
`last_error == "ValueError: bad row CMDCANARY-curl-H-Bearer-s3cr3t"` and the worker's `job failed` log line carries the
same text in `exception`. SQLAlchemy statement errors (the engine does not set `hide_parameters`) also append
`[parameters: ...]`; for `replace_run` those are evidence JSON and `agent_slug`, i.e. metadata, so the write path is a
P3 note, not a leak.

Fix. Move the two `findings_inputs` calls and `load_run` inside the `try` (or wrap the whole body of `detect_run` after the
lock) so any `Exception` becomes `DetectorError("analysis", n) from None`; keep the `DetectorError` re-raise. Add a
regression test modelled on probe 3. Consider `hide_parameters=True` on the engine (a separate, repo-wide decision).

**Resolution** (d59cf4e): `detect_run` wraps the whole job body; any other exception becomes `DetectorError("job", 0)` `from None`, only the class is logged; test `test_a_failure_while_loading_events_dead_letters_with_fixed_text` (fails without the wrapper). `hide_parameters` left as a repo-wide decision.

### P2-2. A long loop on a successful run is `critical`; ADR-081 says it is `info`

Location: `findings/detectors/sequence.py:100-106` (`reps >= 10 -> CRITICAL` before the success check);
`docs/decisions/ADR-081:24-25` ("A probable loop on a successful run is `info`"); the plan's D7 says the same; the plan's
detector table says "`info` if the run succeeded and repetitions < 6, `warning` otherwise, `critical` at >= 10";
`health.py:140-141` turns a `critical` loop into `loops: bad` and `overall: bad`.

Problem. The accepted decision text and the code disagree, and the code's reading is the one that hurts trust: a coding
agent that successfully does `read -> edit -> test` over twelve files is reported "Probable: the agent may be looping",
`critical`, and its health reads `bad`. The `looping` fixture itself is a successful run whose 11 repetitions are
`critical` (`looping.expected.json`), so the fixture pins the table's reading, not the ADR's. Plan risk 1 lists exactly
this false positive and names "`info` on successful runs" as the mitigation.

Fix. Decide one way and make the ADR, plan table, code and fixture agree. Recommended: on a run whose status is `SUCCESS`,
cap the sequence finding at `warning` (never `critical`), keep `info` below 6 repetitions; keep `critical` for active and
failed runs. If the table's reading is kept instead, correct ADR-081 and D7 and say in the runbook that a successful run
can read `bad` on loops.

**Resolution** (0b48adb): the ADR was right; the code, the plan table and the runbook now make a probable loop on a successful run `info` at any length (`warning`/`critical` only when the run did not succeed). `looping.expected.json` and `two_agents.expected.json` changed (both scenarios are successful runs), web fixture and tests mirrored; parametrized test covers successful and failed/blocked looping runs.

### P2-3. The shrink loop in `validate_evidence` is dead code and no test exercises it (surviving mutation M5)

Location: `findings/model.py:366-374`.

Problem. Mutation M5 (make the `while` loop exit unconditionally, so nothing is ever halved and neither the 50-id nor the
15 KiB bound is enforced by the loop) survives `test_maximum_size_client_strings_cannot_make_evidence_too_big`,
`test_a_run_never_cites_more_than_fifty_events_per_finding` and `test_bounds_on_a_run_engineered_to_produce_thousands_of_candidates`.
Reason: under `LIST_LIMITS` (`event_ids` 48, `chain`/`series` 50 whose start and end are members, `failures` 20,
`by_scope` 20 x 2 ids) and 128-character labels, no detector can cite more than 50 ids or exceed about 10 KiB, so the
loop never runs. The plan's AC-2 and the implementation note ("halves the longest list until it fits") describe a safety
net that is untested. If a future evidence key or a larger list limit relies on it, a bug there would surface only as the
database CHECK and a dead letter.

Fix. Either delete the loop and state that the bounds hold by construction (an assertion that raises `EvidenceError`
when they do not, which `run_all` turns into a `DetectorError`), or keep it and add a direct unit test of
`validate_evidence` with an evidence object that exceeds the bounds before trimming (for example a `chain` of 50 plus a
`span_lineage` of 20 and a 64-character `event_type` per entry does not reach it; the test would need a temporary
relaxation of `LIST_LIMITS` via monkeypatch). The first option is simpler and honest.

**Resolution** (78eb4bb): the shrink loop was deleted; `validate_evidence` raises `EvidenceError` past 50 ids or 15 KiB; `test_validate_evidence_refuses_evidence_that_breaks_the_bounds` relaxes `LIST_LIMITS` to reach both checks and fails if either check is disabled.

### P3 (optional)

- **P3-1 (filed KI-097). Health reads `no_data` for `loops`/`context` for the whole life of an active run.** Every batch re-summarises and
  re-enqueues `detect_findings`, so `job_states` reports `processing` almost continuously while events arrive and
  `_status` ranks `processing` first (`service.py:80-81`); the findings list shows the previous analysis but the two
  finding-based dimensions say "Analysis in progress." until the run ends. By design (ADR-081: `no_data` unless current),
  but the UI could read the previous analysis when `computed_at` is set, labelled as such.
- **P3-2 (resolved a391df1). `reported_loop` without `loop.repetitions` renders "repeated None times".** `loop.detected` has no required
  attributes (`registry.py:232`); `reported.py:43` stores `None`; `describe.py:44` formats it. Say "an unknown number of
  times" when the value is missing.
- **P3-3 (filed KI-098). The E2E verifies findings only after a settle `rebuild-findings`.** `findings-e2e.sh` waits for `current`, rebuilds
  once (to settle the cost-spike baseline), then asserts and compares a second rebuild with a snapshot. A detector that is
  non-deterministic in arrival order would be hidden if its first-pass output differed; the unit test covers this for the
  fixtures, but the E2E could also snapshot before the settle rebuild and compare everything except `cost_spike`.
- **P3-4 (not done; the project-bound probe is now a test, a391df1). `badges`, `for_run` and the stale delete cannot be shown wrong by a mutation.** Run ids are globally unique UUIDs
  and the page's ids are already scoped, so dropping `workspace_id` from `badges` (`repository.py:247`) or from the stale
  delete (`:162-163`) would change nothing today. INV-3 is satisfied and documented as a rule, not a test; a cross-tenant
  test that reuses one run id in two workspaces (the FK allows it) would pin it.
- **P3-5 (filed KI-099, runbook note a391df1). Truncated runs fold their status from the prefix.** A finished 60,000-event run reads `RUNNING` to the detectors
  (`build.py:31-37` over the first 50,000), so `cost_spike` never runs on it, the chain only fires if `run.failed` is in the
  prefix, and a loop is `warning` rather than `info`. The state says `truncated`; the runbook could say what that implies.
- **P3-6 (covered by the P2-1 wrapper, d59cf4e). `last_error` for a `replace_run` failure quotes bind parameters.** SQLAlchemy appends `[parameters: ...]`
  (metadata only: evidence JSON, slugs, ids), truncated to 2,000 characters. Not a leak; `hide_parameters=True` on the
  engine would make this class of message content-free for every job.

## 5. Mutation checks

Each mutation was applied by exact string replacement, the named test run with `-x`, and the file reverted with
`git checkout` whether or not the test failed. "Killed" means the test failed as expected.

| # | Mutation | Expected failing test | Result |
| --- | --- | --- | --- |
| M1 | `cost_baseline` without the `workspace_id` predicate (`runs/queries.py:207`) | `test_findings_repository.py::test_cost_baseline_never_crosses_the_workspace` | Killed |
| M2 | `try_lock_run` always returns True (no advisory lock) | `test_findings_job.py::test_two_detect_jobs_for_one_run_never_both_write_and_the_loser_comes_back` | Killed |
| M3 | `run_all` raises `DetectorError ... from exc` instead of `from None` | `test_detectors.py::test_a_detector_bug_surfaces_as_a_fixed_message_without_the_cause` (and the job-side canary test) | Killed |
| M4 | `_by_findings` reports `ok` when `state != "current"` | `test_findings_health.py::test_finding_based_dimensions_are_no_data_unless_current[none]` | Killed |
| M5 | `validate_evidence` shrink loop never runs (`if True: break`) | the three bounds tests | **Survived** (P2-3: dead code) |
| M6 | cascade copies `shell.command` into the allowed `error_type` key | `test_detectors.py::test_no_content_reaches_a_finding_even_when_every_event_carries_it[cascade]` | Killed |
| M7 | `_status` returns `current` for an older `detectors_version` | `test_findings_api.py::test_stored_findings_from_older_rules_read_outdated_never_current` | Killed |
| M8 | **the proposed fix**: digest removed from the identical-request key | `test_detectors.py` (67) + the API canary/parity test | Survived: test-neutral, as a fix should be. Conversely, the shipped state (digest in the key) is pinned by no test (P1-1) |
| M10 | capped load ordered by `(sequence, event_id)` / `(occurred_at, event_id)` only | `test_findings_job.py::test_a_capped_load_is_the_canonical_prefix_even_with_ties[True]` | Killed |
| M11 | `summarize_run` no longer enqueues `detect_findings` | `test_findings_job.py::test_a_summary_enqueues_one_coalesced_detect_job_per_run` | Killed |
| M12 | `describe` omits the "Probable:" prefix | `test_detectors.py::test_findings_obey_the_contract[looping]` | Killed |
| M13 | `no_data` ranks above `bad` in `overall` | `test_findings_health.py::test_overall_is_the_worst_status_and_no_data_never_masks_a_warning` | Killed |

Not mutated: `badges`/`for_run` workspace predicates (P3-4 explains why no existing test can see it).

## 6. Probes (throwaway file `apps/api/tests/_probe_review.py`, run once, deleted)

1. **Fingerprint oracle** (P1-1): computed the fingerprint of an `identical_request` finding from its public fields and a
   guessed payload; equal for the right guess, different for a wrong one. VERIFIED.
2. **Project-bound key**: a run ingested under project `alpha` is 200 for the alpha-bound reader, 404 for the beta-bound key
   with an error body identical to a random id's, and `GET /v1/runs` with the beta key lists nothing. VERIFIED correct.
3. **Unwrapped load failure** (P2-1): `row_to_event` raising a canary message puts the canary into `last_error` and the
   `job failed` log line. VERIFIED.

## 7. Disposition requested

Fix P1-1 (one line plus a test and two sentences of ADR-080) before merge. P2-1 is a small move of three statements into
the existing `try`; P2-2 is a decision that must be written down one way or the other; P2-3 is delete-or-test. The P3
items can be filed under the reserved KI range or folded into the runbook. AC-14 can then be closed with this file moved
to `docs/plans/completed/`.
