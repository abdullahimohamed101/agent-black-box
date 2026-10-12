# Phase 11 code review: evaluations, experiments, run comparison (adversarial security and correctness pass)

Reviewer: independent review agent, 2026-10-11. Branch `feature/phase-11-evaluations` at `3fac596`, diff `origin/main...HEAD`.
Scope: the attack list in `phase-11-evaluations.md` (AC-11), INV-1..8, ADR-090..092, `docs/SECURITY.md`, and the oracle rule: for actors
without `payload.read` nothing in a response may depend on withheld content.

Method: read every new API module, the migrations, the authz world/registry, the web proxy and components; ran the focused suites once
(baseline, VERIFIED: 304 passed in 265 s on a throwaway database created from `ABB_TEST_DATABASE_URL=.../abb_test_p11rev`, dropped afterwards);
then 21 mutation checks (break a guard, run the targeted tests, `git checkout` the file) and two reproduction tests for the defects found
(written as untracked files under `apps/api/tests/`, run, and deleted). The tree is clean apart from this file. Nothing was pushed.

## Verdict

No P0. No cross-tenant or cross-role content leak was found: every withholding and projection guard I attacked is tested, and the
mutation that removes it fails a test (table below). Two P2 correctness defects (CONFIRMED by reproduction) and a handful of P3 test
gaps and nits.

## Findings

### P2-1 (CONFIRMED) A judge cooldown refusal restarts the cooldown: any trigger during the window extends it indefinitely

- Where: `apps/api/src/abb_api/evaluations/job.py:102-110` writes the refusal as `failed/JUDGE_BUDGET`; `apps/api/src/abb_api/evaluations/repository.py:218-234`
  (`judge_cooling_down`) looks for *any* `failed` row with `error_code = 'JUDGE_BUDGET'` and `completed_at > now() - cooldown`.
- Impact: availability of a judge evaluator. The refusal row satisfies the cooldown predicate itself, so the window is measured from the last
  *refusal*, not from the expensive reply. One expensive reply plus one trigger every < `ABB_JUDGE_COOLDOWN_SECONDS` (300 s) keeps the evaluator refused
  for ever, and each refusal burns one of the 20 rows per (run, evaluator). `POST /v1/experiments/{id}/evaluate` makes this automatic: it
  "retries failed pairs" (`experiments/service.py:495-498`), so a person (or a script) clicking "Evaluate all" every few minutes re-arms the cooldown and
  fills the per-pair bound with `JUDGE_BUDGET` rows without a single provider call. With a real provider (KI-100) the same loop means the evaluator never
  recovers on its own.
- Failure scenario (reproduced): cooldown 2 s; expensive reply at t0 (`JUDGE_BUDGET`); trigger at t0+1.2 s (refused, correct); trigger at t0+2.4 s
  (**refused again**, expected to run). Repro test asserted `[('failed','JUDGE_BUDGET')]*3`; a correct implementation gives `completed` for the third.
  The existing `test_a_reply_over_the_cost_bound_refuses_the_evaluator_for_the_cooldown` cannot see this: it only checks "refused inside the window" and
  "works with cooldown 0".
- Fix: make the refusal distinguishable and exclude it from the cooldown predicate, e.g. a separate `error_code` (`JUDGE_COOLDOWN`) or match on the
  stored message, or better: record the cooldown from the *expensive* row only (`error = "The judge reply cost more..."`), and have `judge_cooling_down`
  filter `error_code = 'JUDGE_BUDGET' AND error <> '<refusal text>'`. Also consider not retrying `JUDGE_BUDGET` pairs from evaluate-all (they are
  a bound, not a transient failure). Add the three-trigger timing test above (it fails today). Note `JudgeBudgetExceeded` is also raised for "prompt
  longer than max_input_chars" (`judge.py:327-328`) with the same code; `build_prompt` makes that unreachable, but if it ever fires it also arms the
  cooldown; give it its own code.

### P2-2 (CONFIRMED) A row the API reports as terminal `failed/JOB_FAILED` later becomes `completed` (the derived state is not stable)

- Where: `apps/api/src/abb_api/evaluations/runs.py:65-77` (`_derived`) answers `failed/JOB_FAILED` for a `pending` row whose job is dead-lettered or
  stale; `runs.py:187-190` lets a new trigger insert a fresh row and enqueue a new job; `job.py:165` (`claim_pending`) then claims **every** pending row
  of the run, including the one already shown as failed.
- Impact: the API contract says terminal states are immutable (ADR-090, the response description at `run_router.py:44-49`, the runbook "a failed row is
  history"). A reader (UI, a script that keys on `state == failed`, an audit) sees `failed/JOB_FAILED` for row A, and later `completed` for the same id
  with a score; the run then shows two completed rows for one evaluator where the user expected one retry. The same flip happens with the stale rule:
  a row older than `ABB_EVAL_STALE_SECONDS` reads `failed`, a trigger re-enqueues a job, the row reads `pending` again (job state `processing`), then
  `completed`. Not a security issue (nothing leaks; INV-1 holds at the database); a correctness/contract defect in the derived view.
- Failure scenario (reproduced): trigger; dead-letter the job; `GET` shows `failed/JOB_FAILED`; trigger again (202); drain; the first row is
  `completed` in the database and the listing shows `[completed, completed]`. The existing
  `test_a_pending_row_whose_job_was_dead_lettered_reads_as_failed_without_a_write` stops one step before the drain.
- Fix (pick one): (a) when the trigger decides a live row is dead (`_derived(...).state == "failed"`), write it `failed/JOB_FAILED` for real inside the
  same transaction (the row is still `pending`, so the trigger allows it; this is "a derived record", not an event, INV-1 is untouched) before inserting the
  new row; or (b) have `claim_pending` skip rows older than the stale limit and fail them explicitly. (a) is simpler and makes the view and the row agree.
  Add the drain step to the dead-letter test and assert the first row stays `failed`.

### P3-1 (CONFIRMED by mutation) Tenant predicates on three repository reads are not covered by any test

Mutations that survived the full targeted suites (nothing failed):

| Mutation | File:line | Suites run | Result |
| --- | --- | --- | --- |
| drop `workspace_id` from `EvaluationRepository.claim_pending` | `evaluations/repository.py:193-197` | handler, api, isolation (38 tests) | survived |
| drop `workspace_id` from `CostRepository.by_model` | `comparison/repository.py:28` | comparison api + cross-workspace (70) | survived |
| drop `er.workspace_id = :ws` from `_PAIRS` | `experiments/repository.py:175` | experiments api (16) | survived |

Impact: none exploitable today (run and experiment ids are ULIDs unique across tenants, and every public route resolves the run or experiment through a
tenant-scoped query first), but INV-3 is a repository-level invariant and `test_the_evaluation_reads_never_cross_the_tenant_even_with_a_foreign_run_id`
covers `EvaluationReads` only. Fix: extend that test to `EvaluationRepository.claim_pending`, `CostRepository.by_model` and `ExperimentRepository.pair_states`
with a foreign run/experiment id (same pattern: expect `[]`).

### P3-2 (CONFIRMED by mutation) The viewer's `error` text withholding is untested

- Where: `apps/api/src/abb_api/evaluations/runs.py:115` (`error=record.error if reads else None`). Mutation `error=record.error` survived
  `test_evaluations_api.py` and `authz/test_content_withheld.py` (58 tests).
- Impact: low. Every stored `error` is templated (`job.py:52-63`, never `str(exc)` of a foreign exception) so the field carries no captured content;
  the guard is defence in depth. Fix: seed a `failed` row in the authz world (or in `test_a_viewer_sees_scores_and_labels_but_never_free_text`) and assert
  `error is None` for `WITHHELD_FROM` actors.

### P3-3 (CONFIRMED by mutation) `project_mismatch` in evaluate-all is unreachable and untested

- Where: `apps/api/src/abb_api/experiments/service.py:451-452`. Mutation (remove the branch) survived `test_experiments_api.py`.
- Why unreachable: `create` refuses an evaluator of another project (`service.py:221-224`) and an evaluator's `project_id` cannot change afterwards.
  Harmless dead code; either delete the branch and the `SkipReason` literal, or keep it and test it by inserting the mismatch directly in the database
  (the skeleton allows it).

### P3-4 (PLAUSIBLE) `tests_passed` trusts the last test event the client sent

- Where: `apps/api/src/abb_api/evaluations/builtin.py:173-181`: the verdict is `test.failed == 0` on the **last** `shell.command.{completed,failed}`
  carrying `test.framework`. Both the attribute values and the order are client-controlled telemetry (the SDK's parser writes them, but any sender can).
- Impact: an agent that runs the suite (fails), then runs `pytest tests/test_nothing.py` (passes) last scores `pass`; a hostile SDK user can stamp
  `test.failed: 0` on any command. This is the inherent trust model of every evaluator over telemetry (`run_succeeded` trusts `run.completed` the same
  way), and `ground_truth: true` on deterministic rows overstates it. Not a bug in this phase; worth one sentence in `docs/SECURITY.md` ("evaluations
  are derived from telemetry the sender controls; `ground_truth` means 'not a model opinion', not 'verified'") and a KI next to KI-107.

### P3-5 (PLAUSIBLE) `CallBudget` is a per-call no-op and its test cannot fail through the evaluator

- Where: `apps/api/src/abb_api/evaluations/judge.py:330` (`CallBudget(MAX_CALLS).take()` constructed and consumed in the same statement).
  `test_the_output_bound_and_the_model_call_budget_are_enforced` exercises the class directly, not the evaluator; a second `complete` added to
  `JudgeEvaluator.evaluate` would pass every test. Either hold the budget on the evaluator instance per `evaluate` call and pass it to the adapter
  (where a retry loop would live), or drop the class and the claim in the docstring.

### P3-6 Nits (no behaviour change)

- `apps/api/migrations/versions/0070_evaluators_and_evaluation_state.py:11` says `Revises: 0060`; `down_revision` is `"0080"` (line 20, correct).
- `apps/api/src/abb_api/comparison/router.py:56-62`: a malformed `with_run_id` is a 422 (query pattern) while a malformed path `run_id` is a 404
  (`parse_public_id`). Tested as intended (`test_a_malformed_second_run_id_is_a_422...`) and not a leak (format only), but the two ids of one request
  answer differently for the same mistake.
- `apps/api/src/abb_api/evaluations/runs.py:110` returns `metrics` to every reader unconditionally; correct today because every built-in's keys are
  allowlisted and values are numbers, bools or short strings (`clean_result`, mutation M18 proves the allowlist is enforced), but the human and judge
  kinds have empty allowlists only by convention. A comment or a test that `metric_keys` of free-text kinds stays empty would pin it.

## Attack list results (what was tried, what held)

1. **Viewer comparison projection.** Read `comparison/steps.py`, `compare.py`, `service.py`: keys and labels for actors without `payload.read` are
   `(family|event_type, tool.name | llm.model | span.kind | agent_id | None)`; `workdir_prefixes` (reads `shell.cwd`) is skipped; `file_diff` returns
   per-side counts from `summary.files_modified`, no sets; relations, `divergence`, `first_outcome_difference`, step counts and `truncated_at_event` are
   computed from the same projection and the same `is_step` predicate (projection-independent). `cost_by_model` and evaluation diffs carry only
   model names, scores and labels, which VIEWER already sees. Mutations M1 (drop `holds`), M2 (command text in the metadata key), M3 (`files.both`
   for a viewer), M14 (drop the project scope on `_visible`) each fail a test. Foreign, random and other-project `with_run_id` are one `RUN_NOT_FOUND`
   body (`test_foreign_missing_and_project_scoped_runs_are_one_404`, cross-workspace registry case with `with_run_id` in `RANDOM_IDS`). Held.
2. **Withholding on the 15 routes.** Human/judge `explanation`, `metadata` outside the six scalar keys, `error`, and `allowed_globs|forbidden_globs|
   required_paths|rubric` are withheld; the authz world seeds canaries in a path config, a rubric, a judge explanation and `metadata.raw` in both
   tenants and `test_no_route_returns_command_text_or_paths...` scans all six new GETs plus both compare orders. Mutations M9, M10, M11 fail tests.
   Variant `config` is visible to every reader by design (documented in the route description and `api-v1.md`); the only gap is P3-2 (`error`).
   Judge prompt is never stored. Held.
3. **Evaluator budget and isolation.** `job.py`: no run lock, no writes outside `evaluations`, `load_run(limit+1)` with `RUN_TOO_LARGE`, thread under
   `wait_for`, `eval_timeout < lease/2` validated (`config.py:155-165`), result write in a savepoint, infrastructure errors raise a fixed-text
   `EvaluationJobError`. `claim_pending` is `FOR UPDATE SKIP LOCKED` with a state predicate; `start/complete/fail` carry state predicates (M16 fails
   a test). `test_evaluation_isolation.py` proves the busy-loop case, the lease heartbeat, the byte-identical `runs` row and the absence of a summary
   job. Glob work is bounded (32 x 256 chars, `**` folded, `fnmatch.translate` in 3.9+ is backtracking-safe, budget per pattern and per 256 paths).
   Residual (stuck thread occupies the executor) is recorded in ADR-090. Held.
4. **Judge prompt and parser.** Instructions and rubric first; one line of ASCII JSON with `<`/`>` escaped between fixed markers; the fake reads
   `rfind(OPEN)` and the labels line only from the head; the parser clamps the score, allowlists the label, truncates `explanation` and `raw`.
   Hostile tool/model/span names and a hostile rubric are tested; M8 (drop the delimiter) and M17 (`include_content` ignored) fail tests.
   `JUDGE_BUDGET`/cooldown: bypass not found, but the cooldown is self-extending (P2-1). `ABB_JUDGE_PROVIDER=fake` in production is refused at
   startup (`config.py:167-174`). Held except P2-1.
5. **Evaluate-all bounds.** `experiments/service.py:475-540`: experiment advisory lock, per-run advisory locks taken in sorted order, re-read of pair
   states under the locks, 500 new rows per call, 20 per pair (`at_limit`), archived / human / judge-unconfigured evaluators skipped and reported,
   one coalesced job per run with the batch delay. M12 (ignore open rows) fails a test; `test_evaluate_all_and_triggers_race_without_overshooting_the_row_bound`
   covers the race with single triggers. Project-bound keys cannot reach the route (no key scope has `evaluation.run`). Held (P3-3 is dead code).
6. **Immutability, tenancy, CSRF, XSS.** The `BEFORE UPDATE ... WHEN OLD.state IN ('completed','failed')` trigger is tested for the migration role and
   `abb_runtime` (M15 fails a test); `abb_runtime` is not the table owner so `DISABLE TRIGGER` is not available to it, and the runtime-role test
   enumerates every public table. Every repository method takes `TenantContext` and filters on `workspace_id` (three reads lack a test: P3-1). The web
   proxy applies the exact-`Origin` check, the JSON content-type requirement and the body cap to every non-GET route, and the new routes are plain
   additions to the allowlist (`tests/evaluations-proxy.test.ts` re-proves them). Components render labels, explanations, names and configs as React
   text (no `dangerouslySetInnerHTML`, `href`s are built from route helpers over ids). Web unit tests for the four Phase 11 suites: 49 passed (VERIFIED).
7. **`tests_passed` trust, migrations, INV-1/2/7, test quality.** Trust model: P3-4. Migrations 0070/0071 are additive with server defaults, reversible,
   and `test_a_database_at_0080_upgrades_to_0071_and_back_to_0080` compares tables and columns both ways. INV-1: events untouched, terminal rows
   immutable at the database. INV-2: every row is `(evaluator snapshot, config_snapshot, events)`; the experiment metrics are one read-model statement.
   INV-7: events are read through `PgEventStore.load_run`, costs through a repository; no SQL in routers. Tests that cannot fail: none found beyond
   P3-5; the mutation table shows 17 of 21 mutations caught by the suites the plan names.

## Mutation table

| Id | Guard broken | Caught by |
| --- | --- | --- |
| M1 | comparison `holds(PAYLOAD_READ)` -> always content | `test_comparison_api` (viewer) |
| M2 | command/path in the metadata alignment key | `test_comparison_api`, `test_comparison_align` |
| M3 | `files.both` for a viewer | `test_comparison_api` |
| M4 | `claim_pending` without workspace | **survived** (P3-1) |
| M5 | `CostRepository.by_model` without workspace | **survived** (P3-1) |
| M6 | `_PAIRS` without workspace | **survived** (P3-1) |
| M7 | `error` text shown to viewers | **survived** (P3-2) |
| M8 | judge prompt without the data markers | `test_judge` |
| M9 | human/judge explanation shown to viewers | `test_evaluations_api` |
| M10 | `metadata.raw` shown to viewers | `test_evaluations_api` |
| M11 | path config / rubric shown to viewers | `test_evaluators_api` |
| M12 | evaluate-all ignores pending rows | `test_experiments_api` |
| M13 | evidence ids not checked against the run | `test_evaluators_builtin` |
| M14 | compare ignores the key's project scope | `test_comparison_api` |
| M15 | immutability trigger not installed | `test_evaluation_schema` |
| M16 | `start` without the `pending` predicate | `test_evaluate_handler` |
| M17 | judge sends command text regardless of `include_content` | `test_judge` |
| M18 | `clean_result` metric-key allowlist dropped | `test_evaluators_builtin` |
| M19 | evaluate-all does not skip other-project evaluators | **survived** (P3-3, unreachable) |
| M22 | trigger of a judge with provider `none` allowed | `test_evaluations_api` |
| R1 | repro: cooldown measured from the refusal | fails today (P2-1) |
| R2 | repro: `JOB_FAILED` row completes after a re-trigger | passes today, i.e. the flip happens (P2-2) |

## Environment notes

- All API runs used throwaway databases `abb_test_<random>` created on the compose server through `ABB_TEST_DATABASE_URL` pointed at `abb_test_p11rev`
  (created, then dropped at the end; the random ones are dropped by `conftest.py`). One unrelated `abb_test_3364dbcd8d5f` was present and left alone
  (another worktree's session).
- Not run here: Playwright / E2E, `scripts/quality.sh full` (the plan's closing evidence covers them; nothing in this review changes that).
