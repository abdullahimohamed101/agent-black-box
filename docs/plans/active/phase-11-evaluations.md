# Phase 11 - Evaluations, experiments and run comparison

Status: Planned 2026-10-10 (awaiting the user's decisions below; no code changed)
Owner: implementer agent
Branch: `feature/phase-11-evaluations` (from `main` c212058; worktree `../abb-worktrees/phase-11`)
Depends on: Phase 2 (outbox, run queries), Phase 6 (coding telemetry, artifacts), Phase 7 (cost lines, typed run columns), Phase 15 (`require(action)`, matrix, registry, `payload.read`)
Spec refs: §85 (evaluator contract, isolation, deterministic evaluators, model judges), §86 (experiments, single-variable control, metrics), §98 (comparison views, divergence, noise normalisation), §20 data model (`evaluations`), §41/§101 (`POST /v1/runs/{id}/evaluations`), §59.4 (research persona), §60.6 FR-EVAL-001..004, §62 INV-1/2/3/5/7, §93 (Class 2 content), §91 (actions)
ADRs: ADR-012 (outbox, derived state), ADR-040..043 (cost lines, typed columns), ADR-061 (actions, `payload.read`), ADR-062 (audit). New: ADR-090, ADR-091, ADR-092 (drafted in the appendix; written at the last step).
Migration ids: 0070, 0071 (chain 0047 -> 0070 -> 0071). ADR ids: 090-092.
Known issues: pulls in none (no open item targets Phase 11); dispositions below.

## Decisions needed from the user (each has a default; "go with your recommendation" suffices)

1. **Who may evaluate.** Default: people only. `evaluation.run` (trigger an evaluator) and `evaluation.label` (human verdicts): OWNER, ADMIN, DEVELOPER. `evaluator.write` (define/archive evaluators) and `experiment.write`: OWNER, ADMIN, DEVELOPER. `evaluation.read`/`experiment.read`: every role with `run.read` (VIEWER sees scores and labels, never free text) and `runs:read` keys. **No new API-key scope** this phase (an `evaluations:write` scope for scripted experiment runners is filed as a KI; it needs a CHECK-constraint migration and matrix work that would collide with the parallel phases).
2. **Automatic evaluation when a run finishes.** Default: **not in this phase.** Evaluations are started by a person (run page), or for every run of an experiment at once (`POST /v1/experiments/{id}/evaluate`). Auto-run needs a hook in `jobs/handlers.py::summarize_run`, the file Phase 10's detectors also hook; filed as a KI with the hook design.
3. **Model judge provider.** Default: interface plus a deterministic fake only (`ABB_JUDGE_PROVIDER=none|fake`, default `none`). No vendor HTTP client, no key. A judge evaluator cannot be created when the provider is `none` (`409 JUDGE_NOT_CONFIGURED`). Content is **not** sent to a judge unless the evaluator's config says `include_content: true` (INV-5); the fake needs none.
4. **The two comparable runs.** Default: the coding-agent example gains `--variant baseline|wrong-fix` (scripted; `wrong-fix` never applies the second patch, so the tests fail and the run ends `FAILED`); `scripts/eval-e2e.sh` records both, builds an experiment, evaluates, and Playwright checks the comparison and the divergence point.
5. **What a VIEWER's comparison aligns on.** Default: actors without `payload.read` get an alignment keyed on kinds and tool names only (no command text, paths or model names in the keys), so the response never acts as an equality oracle on withheld content. The divergence point may therefore differ by role; the response says `alignment: "metadata"` or `"content"`.

## Outcome

A person opens a run, sees its evaluations (deterministic checks, metric thresholds, human verdicts and clearly labelled model-judge opinions), starts
another, or records their own verdict. An evaluation has its own state (`pending`, `running`, `completed`, `failed`); nothing it does, including an
evaluator that crashes or times out, changes the run's status, summary or events. A person groups runs into an experiment with a baseline and
candidate variants, each with a declared configuration; the experiment page shows success rate, cost, latency, retries, tool calls, failure
categories and evaluation scores per variant, and says plainly when more than one variable changed between baseline and candidate (§86). Any two
runs can be compared: summary deltas, steps aligned by what the agent did (ids, timestamps and other noise normalised), the first point where the
runs diverge, differences in files, cost by model and evaluation scores (§98). VIEWER and BILLING never receive command text, paths or free text
through any of it.

## Non-goals

Automatic evaluation on run completion (decision 2); a real model-judge provider (decision 3; KI); SDK-triggered evaluation and an
`evaluations:write` scope (decision 1; KI); task datasets and an experiment *runner* that executes agents (§86 "task dataset" is a label on the
experiment, not a thing this product runs; §59.4's "execute N runs" stays the user's script); evaluator plugins loaded from user code
(evaluators are built in and configured; a plugin interface is a later ADR); Level 2/3 replay (Phase 12); findings/detectors (Phase 10; the
`evidence_event_ids` convention is shared, see Merge notes); search over evaluations (Phase 16); alerting on scores (Phase 17); evaluation
retention (Phase 19, with KI-021/KI-040); per-step cost attribution beyond what `cost_calculations` already holds; event-sequence diff
rendering below the step level (§98 "event sequence differences" is the aligned-steps view; the drawer shows a step's events as today). Nothing
here needs Kafka/ClickHouse/Redis/Kubernetes.

## Current architecture (verified by reading the code unless marked ASSUMED)

- **Storage.** `evaluations` exists since migration 0005 (`db/tables.py:618`): `(workspace_id, id)` pk, `run_id` (composite FK to `runs`, `ON DELETE
  CASCADE`), `evaluator_name`, `evaluator_version`, `score float`, `label text`, `metadata jsonb`, `created_at`. No code reads or writes it. No
  experiment tables exist. Migrations run 0001-0009, 0040-0047; `test_migrations.py` pins the exact revision list and has a "database at 0044
  upgrades to head and back" test. `APPEND_ONLY_TABLES = ("events", "audit_log")` is exported by 0047 and asserted by `test_runtime_role.py:117`;
  every other table must be fully writable by `abb_runtime`.
- **Derived state.** `runs/summary.py::derive_run` is a pure function of the events (ADR-012); `runs` carries typed copies (`cost_usd`, `retry_cost_usd`,
  `llm_calls`, `tool_calls`, `retry_count`, `files_modified`, `tool_spans_*`, `llm_spans_*`) and `summary` JSONB (`models`, `error_count`,
  `estimated_cost_usd`, ...). `spans` has `name`, `kind`, `agent_slug`, `status`, `started_at`, `ended_at`, `duration_ms`, `event_count`.
  `cost_calculations` has one row per priced model call (`model`, `provider`, `total`, `is_retry`, `span_id`). Run metadata given to the SDK
  (`bb.run(..., metadata={"model": ...})`) becomes `metadata.<key>` attributes on `run.started` (`sdk client.py:307`) and is **not** copied into
  `runs.metadata` (only `POST /v1/runs` fills that column). `agent_version` is an envelope field on events, not on `runs`.
- **Jobs.** `jobs/outbox.py`: tenant-scoped `OutboxRepository.enqueue(job_type, dedupe_key, payload, delay)` coalescing on `(job_type, dedupe_key)`
  while pending; `JobQueue.claim` with leases, heartbeat, backoff, dead letter after `worker_max_attempts` (5). `jobs/handlers.py::HANDLERS` maps
  `summarize_run` and `refresh_analytics_day`; a handler runs inside the transaction that completes the job (`jobs/worker.py:98-101`); an exception
  retries the job. `PgEventStore.load_run` loads every event of a run with no cap (KI-022).
- **Authorization.** `require(action)` (`authz/dependencies.py`) runs before any lookup; `holds(principal, action)` shapes responses that vary by
  permission; the matrix is data (`authz/matrix.py`) mirrored by a literal table (`tests/authz/test_matrix_data.py`); every served route needs a
  `RouteCase` (`tests/authz/registry.py`; `test_registry_is_complete.py` walks `app.routes`); `test_role_matrix.py` holds a literal `EXPECTED`
  cell per route x actor; `test_cross_workspace.py` replays every `id_params` with foreign and random ids (`RANDOM_IDS` per parameter name) and scans
  every body for the globex canary, ids and magnitudes. `test_openapi.py` pins `seen == 36` operations and the literal surface set. Mutating admin
  actions write `audit_log` rows (ADR-062). `require(action, owned=True)` plus `authorize_audited(..., resource)` handles `Own(...)` grants.
- **Content withholding.** `runs/content.py`: `withhold_attributes` replaces `CONTENT_ATTRIBUTES` (`shell.command`, `shell.cwd`, `file.path`, `git.*`,
  `http.url`, `test.failing`) and name-heuristic keys with `[withheld]`; `span_name_is_content(kind)` hides names of `shell`/`file`/`git`/kindless
  spans. `event_out`/`_span_out` apply it per actor; `EventOut.withheld_attributes`, `SpanOut.name_withheld`. The web maps these to text
  (`lib/withheld.ts`). `tests/authz/test_content_withheld.py` and the world's `CONTENT_CANARIES` prove no actor without `payload.read` receives them.
- **Routes and the web.** 36 `/v1` operations; `main.py` includes 13 routers and stamps security per operation. The web proxy allowlist is a regex
  table in `apps/web/src/server/upstream.ts:30-42` (GET/POST/PATCH/DELETE per path); fixture mode answers from `src/fixtures/api.ts::fixtureReply`;
  hooks live in `lib/queries.ts` over the generated `lib/api/schema.d.ts`; routes in `lib/routes.ts`; nav in `components/AppShell.tsx` (Dashboard,
  Runs, Analytics, Settings). The run page (`RunDetail.tsx`) has a header with metrics, a story/coding summary, timeline, drawer; no tabs for
  evaluations yet (spec §95.1 names an `evals` tab). The runs list (`RunsList`/`RunsTable`) has filters in the URL and no row selection.
- **Demo data.** `examples/coding-agent` plays a fixed `ScriptedModel` plan (`models.py::_script`: read, search, edit, tests fail, inspect log,
  edit, tests pass, diff, branch, commit, push); `tests/test_demo.py` asserts the story; `scripts/coding-e2e.sh` records one run against a throwaway
  database and runs Playwright (`e2e/coding.spec.ts`). Runs record `metadata={"model": model.name}`.
- **Settings.** `ABB_*` via `core/config.py` (`worker_*`, `analytics_timeout_seconds`, ...). CLI has `jobs-list`/`jobs-retry`.
- **Environment.** `node_modules`, `apps/api/.venv` and the SDK `.venv` are **not installed in this worktree** (same as Phase 15 at planning);
  step 1 starts with `pnpm install` and `uv sync`. Docker/Colima, Postgres on 5433, Node 22 and Chrome are expected per `docs/development/setup.md`
  (UNVERIFIED (env) until step 1 runs `scripts/quality.sh quick`).
- **ASSUMED:** `difflib.SequenceMatcher(autojunk=False)` over 2,000-element lists of short tuples finishes well under a second on the 2-vCPU
  reference VM; step 6 measures it and records the number (AC-7). If it does not, the fallback is a plain O(n*m) LCS in a tight loop with the same cap.

## Known issues considered

- No open item has Target "Phase 11". Open S1 items **KI-018** (unbounded agents/runs/spans) and **KI-019** (no failed-auth throttling) stay
  deferred to Phase 19; this phase adds no client-controlled unbounded cardinality: per workspace at most 100 evaluators and 200 experiments; per
  experiment at most 10 variants and 500 runs; per (run, evaluator) at most 20 evaluation rows; per run at most 50 human labels (`409 LIMIT_REACHED`).
- **KI-022 / KI-016** (summarizer loads every event, no cap): the evaluation handler loads a run's events through the same store but refuses runs
  above `ABB_EVAL_MAX_EVENTS` (default 50,000) with a `failed` evaluation (`RUN_TOO_LARGE`), never a dead-lettered job and never a worker crash. The
  comparison caps steps at `ABB_COMPARE_MAX_STEPS` (2,000 per side, `truncated: true`). Both are the bounded answer until KI-016's trigger fires.
- **KI-078** (free text shown to VIEWER): evaluator explanations are the new free-text field. Rule: deterministic and metric evaluators build
  explanations from templates with numbers only (a test asserts no content attribute value appears in them); human and judge explanations are
  withheld from actors without `payload.read` (`explanation_withheld: true`). Not a fix for KI-078, which stays Phase 13.
- **KI-044** (real-model demo never run): the real judge provider is likewise unverifiable here; a new KI records that a provider adapter needs the
  user's key and one manual run.
- **KI-021** (finished jobs never purged): `evaluate_run` jobs add to the table at the same slow rate; stays Phase 19.
- **KI-050, KI-055, KI-056, KI-057, KI-017, KI-026, KI-024, KI-040, KI-041, KI-042, KI-067..077** (S2/S3): unrelated, stay as listed.
- **KI-052, KI-061..063, KI-030..035, KI-043, KI-060, KI-013, KI-023, KI-008, KI-010** (S3): unrelated.

## Decisions (D1..D14; ADR drafts in the appendix)

- **D1 (ADR-090) Evaluations are derived, versioned records with their own state; never edits of events or runs.** Each evaluation row is the
  result of (evaluator definition, `evaluator_version`, `config_snapshot`) applied to a run's events at `completed_at`; re-running creates a **new
  row** (the latest per evaluator is what the UI shows by default; history stays). A row's `state` moves `pending -> running -> completed|failed`
  and is never updated after a terminal state (service rule, tested: a second run inserts). Human verdicts are rows with `kind=human`,
  `state=completed` at insert, `requested_by` set; a correction is a newer row. The runtime role keeps UPDATE on `evaluations` (state transitions);
  the table is not added to `APPEND_ONLY_TABLES`. `evaluations.run_id` keeps `ON DELETE CASCADE` (a deleted run takes its derived results).
  INV-1: events untouched; INV-2: every row can be regenerated by re-running the same evaluator version with the stored config snapshot.
- **D2 Evaluator definitions are workspace data, built-in kinds configured by JSON.** `evaluators (workspace_id, id)`: `project_id` (nullable
  composite FK: a workspace-wide evaluator applies to every project), `name` (unique per workspace, slug), `kind` (`deterministic`|`metric`|
  `human`|`llm_judge`, CHECK), `implementation` (the built-in id, CHECK against the registry at the service), `version` (int, the
  implementation's version at creation), `config` JSONB (validated by the implementation's pydantic model; `pg_column_size < 8192` CHECK),
  `description`, `created_by`, `created_at`, `archived_at`. Archiving (`DELETE /v1/evaluators/{id}`) sets `archived_at`; existing results keep
  their snapshot. Built-in implementations (`abb_api/evaluations/builtin.py`), each with a `name`, `version`, `kind`, `Config` model and a pure
  `evaluate(snapshot, config) -> EvaluationResult`:
  - deterministic: `run_succeeded` (status is `SUCCESS`), `tests_passed` (the last `shell.command.completed` carrying `test.framework` has
    `test.failed == 0`; no test run -> `label="unknown"`, score null), `files_touched_only` (every `file.created|modified|deleted` path matches one
    of `allowed_globs`; `forbidden_globs` untouched), `no_policy_blocks` (no `policy.action.blocked`), `expected_files_changed` (every
    `required_paths` entry was modified).
  - metric: `cost_under` (`summary.estimated_cost_usd <= max_usd`), `latency_under` (`duration_ms <= max_ms`), `tool_error_rate_under`
    (`1 - tool_spans_ok/tool_spans_finished <= max_rate`), `retries_under` (`retry_count <= max`), `llm_calls_under`.
  - `human`: no implementation; the API stores what a person submits (`label` from `allowed_labels` in the config, default `pass|fail|unsure`,
    optional `score` in `[0,1]`, `explanation` <= 4,000 chars).
  - `llm_judge`: `judge` (D4).
  Results: `score` in `[0,1]` or null, `label` (<= 64 chars), `explanation` (<= 4,000), `metrics` (flat JSON, <= 32 keys, numbers/strings/bools),
  `evidence_event_ids` (<= 100 `evt_` ids of the run; validated to belong to the run), matching spec §85.1.
- **D3 (ADR-090) Evaluators run in the worker through the outbox, isolated and bounded; failures are results, not retries.** `POST
  /v1/runs/{run_id}/evaluations {evaluator_id}` inserts a `pending` row and enqueues `evaluate_run` (`dedupe_key = <workspace>:<evaluation_id>`,
  payload `{evaluation_id}`) in one transaction. The handler (`jobs/handlers.py::evaluate_run`): marks the row `running` (`started_at`), loads
  the run and its events (refusing above `ABB_EVAL_MAX_EVENTS` -> `failed`, `error_code=RUN_TOO_LARGE`), builds a `RunSnapshot` (run row fields,
  summary, events in canonical order, spans, cost lines), runs the implementation under `asyncio.wait_for(ABB_EVAL_TIMEOUT_SECONDS, default 30)`
  (judges additionally under their own budget, D4), and writes `completed` with the result or `failed` with `error_code` (`EVALUATOR_ERROR`,
  `TIMEOUT`, `RUN_TOO_LARGE`, `JUDGE_UNAVAILABLE`, `JUDGE_BUDGET`) and a bounded `error` text (<= 2,000 chars, never event content: the message is
  the exception type and the evaluator's own message, which templates compose from numbers). The job itself only fails (and retries) on
  infrastructure errors (database connectivity); an evaluator exception is caught and recorded, so a bad evaluator cannot dead-letter anything or
  loop. The handler **never writes `runs`, `spans`, `events` or `cost_calculations`** (a test snapshots the run row and its summary before and after
  a crashing evaluator and asserts equality; another asserts no `summarize_run` job was created). A run still `processing` (summary not current)
  evaluates against its current events and records `metrics.summary_state`; the UI warns. Pending rows older than `ABB_EVAL_STALE_SECONDS`
  (default 3,600) whose job is dead-lettered show `state=failed` on read (`error_code=JOB_FAILED`) without a write (derived, like
  `summary_state`).
- **D4 (ADR-091) Model judges sit behind a provider interface; the only shipped provider is a deterministic fake; judges are opinions, labelled.**
  `evaluations/judge.py`: `JudgeModel` Protocol `complete(prompt: str, *, max_output_tokens: int) -> JudgeReply(text, model, input_tokens,
  output_tokens, cost_usd | None)`; `FakeJudgeModel` (model name `fake-judge-1`, `cost_usd=0`) scores from the rubric's keywords and the step
  list deterministically (same input -> same output; a `seed` is not needed). `ABB_JUDGE_PROVIDER=none|fake` (default `none`; `fake` is
  refused unless `ABB_ENVIRONMENT in {development, test}` **or** the operator also sets `ABB_JUDGE_ALLOW_FAKE=1`, so a fake never silently
  judges production runs). A judge evaluator's config: `rubric` (<= 4,000 chars), `prompt_template_version` (fixed string from the
  implementation, recorded), `include_content: bool = false` (INV-5: with `false` the prompt holds the same projection a VIEWER sees: kinds,
  tool names, statuses, counts, durations, model names; with `true` it adds command text, paths and inline payload excerpts, truncated), `max_input_chars`
  (default 60,000, hard cap 200,000), `max_cost_usd` (default 0.50; a provider reply above it is recorded as `failed` `JUDGE_BUDGET` after the fact,
  and the next judge call for that evaluator is refused for `ABB_JUDGE_COOLDOWN_SECONDS`). The result's `metadata` records `judge_model`,
  `prompt_template_version`, `input_tokens`, `output_tokens`, `cost_usd`, `provider`, `raw` (first 2,000 chars of the reply). The API and UI
  mark these results `kind=llm_judge` with `ground_truth: false` on every representation; an experiment's metric table shows judge scores in
  their own column group titled "Model judge (not ground truth)". A real provider adapter (Anthropic Messages over `httpx`, which the API already
  has) is a later step with the user's key: filed as a KI with the interface it must implement. No vendor SDK is added.
- **D5 Experiments are declared variants with declared configurations; the control check is computed on read.** Tables: `experiments
  (workspace_id, id)`: `project_id` (composite FK, required: an experiment compares runs of one project), `name` (<= 128), `description`
  (<= 2,000), `dataset_label` (<= 128, free label for §86 "task dataset"), `evaluator_ids` (JSONB array of up to 20 evaluator ids of the
  workspace, validated), `created_by`, `created_at`, `archived_at`. `experiment_variants (workspace_id, id)`: `experiment_id` (composite FK,
  cascade), `name` (<= 64, unique per experiment), `role` (`baseline`|`candidate`, CHECK; exactly one baseline, enforced by a partial unique index),
  `config` JSONB (a flat map of <= 32 string keys, values <= 256 chars: `model`, `prompt_version`, `agent_version`, ... whatever the user declares),
  `created_at`. `experiment_runs (workspace_id, experiment_id, run_id)` pk, `variant_id` (composite FK), `added_by`, `added_at`; a run may be in
  several experiments but once per experiment; the run must belong to the experiment's project (`422 RUN_PROJECT_MISMATCH`). On read, for each
  candidate: `changed_variables = keys where config differs from the baseline (missing counts as different)`, `controlled = len(changed_variables)
  == 1`; `warnings`: `not_single_variable` (per candidate), `variant_models_vary` (a variant whose runs' `summary.models` are not all equal, the
  observed check that catches a declared `model` that the runs contradict), `variant_agents_vary` (different `agent_slug`), `unevaluated_runs`.
  Metrics per variant, from `runs` and the latest completed `evaluations` per (run, evaluator): `runs`, `success_rate` (status `SUCCESS` over
  terminal runs), `cost_usd` mean/median/p95 (`percentile_cont` over at most 500 rows), `duration_ms` mean/median/p95, `retry_rate`
  (`retry_count > 0` share), `tool_calls` mean, `failure_categories` (count by non-SUCCESS status plus `error_count > 0` among successes as
  `recovered`), `evaluations[evaluator_id] = {completed, mean_score, pass_rate (label == "pass"), failed}`. All computed in one SQL statement per
  experiment inside `ExperimentRepository` (INV-7), under `ABB_ANALYTICS_TIMEOUT_SECONDS`-style statement timeout (reuse the analytics timeout
  setting).
- **D6 (ADR-092) Comparison is computed on read from derived data, deterministic, bounded, role-aware; not stored.** `GET
  /v1/runs/{run_id}/compare?with=<run_id>` (both runs visible to the caller; same workspace; either order; comparing a run with itself is allowed and
  trivially aligned). The service builds a **step list** per run from the run's events in canonical order (`sort_events`, never arrival order):
  one step per `*.completed|*.failed` lifecycle event (tool, llm, shell, db, span, agent) and per point event in the file, git, reliability,
  security and approval families; `*.started` events, `run.*`, `http.*` and `custom.*` are not steps (custom types are counted per name in the
  summary deltas instead). A step carries `event_id`, `span_id`, `family`, `event_type`, `status`, `duration_ms`, `offset_ms` from run start, a
  display `label` and an alignment `key`. **Content alignment** (actors with `payload.read`): `key = (family, normalised name)` where the name is
  `tool.name` / `llm.model` / `file.path` relative to the run's first `shell.cwd` or repo root / `shell.command` / `git.branch` / `span.name`,
  normalised by: trimming and collapsing whitespace; replacing ULIDs, UUIDs, hex strings >= 8 chars, ISO timestamps, durations, port numbers and
  runs of digits >= 4 with `#`; dropping absolute temp-dir prefixes (`/tmp/...`, `/var/folders/...`, the run's own workdir); lower-casing. Status
  is **not** part of the key (a failed and a successful attempt of the same action align, then show as `changed`). **Metadata alignment**
  (actors without `payload.read`): `key = (family, tool.name | llm.model | span.kind | None)`; command text, paths and span names of content kinds
  are absent from keys and labels (`label_withheld: true`). Alignment: `difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False).get_opcodes()`
  (deterministic, stdlib, LCS-like) over at most `ABB_COMPARE_MAX_STEPS` steps per side (2,000; beyond it both lists are cut and `truncated: true`,
  the response says at which offset). Output: `steps: [{relation: equal|changed|left_only|right_only, left?, right?}]` where `changed` is an aligned
  pair whose `status` or `label` differs; `divergence`: the index and both steps of the first non-`equal` row (null when none), plus
  `first_outcome_difference`: the first aligned pair whose status differs (often earlier than a structural divergence: the test that failed); `summary`:
  both runs' `status`, `duration_ms`, `estimated_cost_usd`, `retry_cost_usd`, `llm_calls`, `tool_calls`, `retry_count`, `error_count`,
  `files_modified`, `input_tokens`, `output_tokens` with `delta` (right minus left); `cost_by_model`: per model `{left, right, delta}` from
  `cost_calculations`; `files`: `{only_left, only_right, both}` of modified paths (counts only for metadata actors, with `paths_withheld: true`);
  `evaluations`: per evaluator present on either run, the latest completed `{left: {score,label}, right: {...}}` (explanations never included here);
  `alignment: content|metadata`. Tests: identical runs (all `equal`, divergence null); the demo pair (divergence at the second `edit_file`,
  `first_outcome_difference` at the second test run); reordered **arrival** (same result); timestamp skew of one side by hours (same alignment,
  offsets differ); renamed ids/temp dirs (equal); an inserted loop of 50 repeated steps on one side (one `right_only` block, divergence at its
  start); 10,000-step runs (`truncated`, response under the budget in AC-7); property test (hypothesis is not a dependency; a seeded `random` loop in
  the test) that alignment of `(a, b)` mirrors `(b, a)`.
- **D7 Viewers never receive withheld content through evaluations, experiments or comparison.** Applied at the service, with `holds(principal,
  PAYLOAD_READ)`: comparison per D6; `EvaluationOut.explanation` is null with `explanation_withheld: true` for `human` and `llm_judge` rows (free
  text) and for any row whose implementation declares `explanation_is_content` (none of the built-ins); `metrics` of built-ins are numbers and
  enum strings only (a test iterates every built-in over the content-canary run and asserts no canary in any field); `evidence_event_ids` are ids
  (the event endpoints already withhold). Experiment variant `config` values are user-declared labels (`model`, `prompt_version`), visible to
  every reader, documented as such in `api-v1.md` ("do not put secrets or prompts in variant configs"; values <= 256 chars). The judge prompt with
  `include_content: true` is content: never stored, never returned (`metadata.raw` is the reply, truncated; the reply of a real provider could echo
  content, so `metadata.raw` is withheld from actors without `payload.read` as well).
- **D8 Actions and matrix.** New actions in `authz/actions.py`: `evaluation.read`, `evaluation.run`, `evaluation.label`, `evaluator.write`,
  `experiment.read`, `experiment.write`. Grants per decision 1 (table below). `runs:read` keys gain `evaluation.read` and `experiment.read`
  (reads only; `SCOPE_ACTIONS` and the literal `EXPECTED_SCOPE_ACTIONS` both change). No `Own(...)` grants. Every new route gets a `RouteCase`, a
  literal `EXPECTED` row, `id_params` with `RANDOM_IDS` entries for `evaluator_id`, `experiment_id`, `variant_id`, `evaluation_id`, and the
  `with` query parameter (a run id) is listed as an id param so the cross-workspace test replays it with globex's run id (404, identical body) and a
  random one. `probe_actors` for mutating routes: `("owner", "dual")` like today's admin cases.
- **D9 Audit.** Rows (ADR-062) for: evaluator create/archive, experiment create/archive, variant create, run attach/detach, evaluate-all, and a
  human label (`action=evaluation.label`, `resource_kind=evaluation`, details `{run_id, evaluator_id, label}`; never the explanation). Triggering a
  single evaluation is **not** audited (it is a read-like derived computation and bounded per run; a flood is capped by the 20-rows rule).
- **D10 Bounds** (all `409 LIMIT_REACHED` with `details.limit`): 100 evaluators, 200 experiments per workspace; 10 variants, 500 runs, 20
  evaluators per experiment; 20 rows per (run, evaluator) (a re-run beyond it is refused), 50 human rows per run; `evaluate-all` enqueues at most
  `runs x evaluators` jobs that have no completed row yet, capped at 2,000 per call (`details.enqueued`, `skipped`), coalesced by dedupe key so a
  double click costs nothing. Request bodies are validated with max lengths; `config` and `metrics` sizes are checked by pydantic and by DB CHECKs.
- **D11 Web.** Run page: an **Evaluations** section under the header (spec §95.1 `evals` tab): rows with kind badge (`Deterministic`, `Metric`,
  `Human`, `Model judge - not ground truth`), state (`pending`/`running` with a spinner that polls every 2 s while any row is non-terminal,
  `completed` with score/label, `failed` with the error code), explanation (or "withheld for your role"), evidence links that select the event in
  the timeline; a "Run evaluator" select (enabled evaluators of the project/workspace) and a "Record verdict" form (label select, optional score,
  explanation), both shown only when `permissions` include the action. Runs list: a "Compare" checkbox per row (at most two; the second selection
  enables a "Compare" button -> `/w/{ws}/projects/{p}/runs/compare?a=<id>&b=<id>`). Compare page: summary metrics table (left, right, delta with
  sign and colour **plus** an arrow glyph), a "Jump to divergence" button, two-column aligned steps (virtualised with the existing timeline
  virtualisation helper; equal rows muted, `changed` highlighted, `left_only`/`right_only` with an empty cell on the other side, divergence row
  marked and announced with `aria-live`), evaluation differences, cost by model, files (or counts with the withheld notice); `truncated` banner.
  Experiments: `/w/{ws}/projects/{p}/experiments` (list, create form with name/description/dataset label/evaluators), `/experiments/{id}` (variants
  with configs; the **single-variable banner**: "Not a controlled experiment: `model` and `prompt_version` both differ between baseline and
  candidate-2"; observed warnings; the metrics table per variant with the judge column group; attach runs by picking from the project's recent
  runs; "Evaluate all"; per-run rows link to the run and to "compare with baseline run"). Nav: `Experiments` link in `AppShell` after `Analytics`.
  No business logic in components: control checks, metrics and alignment come from the API; the UI formats. Fixture mode gets deterministic
  fixtures for every new endpoint (`src/fixtures/evaluations.ts`), so `pnpm test` and `fixtures.spec.ts` cover the pages without a server.
- **D12 The second demo run.** `examples/coding-agent/coding_agent/models.py` gains `ScriptedModel(variant="baseline"|"wrong-fix")`:
  `wrong-fix` replays the baseline up to the first failing test run, then "fixes" the wrong line (a no-op edit of `app/oauth.py` that changes a
  comment), runs the tests again (still one failure), and reports failure; `run_agent` ends the run with `run.completed` status `error`. The run's
  metadata records `{"model": ..., "variant": ...}`. `run_demo.py --variant` and `tests/test_demo.py` cover both stories. `scripts/eval-e2e.sh`
  (pattern of `analytics-e2e.sh`: own database `abb_p11`, API on :8160, worker, built web on :3160, a dev session) records both variants, creates
  two evaluators (`tests_passed`, `cost_under`) and an experiment with `baseline` (`config {"model":"scripted-agent-1","variant":"baseline"}`) and
  `wrong-fix` (two keys differ -> the warning is expected and asserted), attaches the runs, evaluates all, waits for `completed`, and runs
  `e2e/evaluations.spec.ts`: the run page shows both evaluations, the experiment page shows the banner and the table, the compare page's
  divergence row is the second `edit_file` and `first_outcome_difference` is the second test run; screenshots to `docs/screenshots/phase-11/`.
  CI job `eval-e2e` (one commit touching `ci.yml` and `Makefile`).
- **D13 Settings.** `ABB_EVAL_TIMEOUT_SECONDS` (30), `ABB_EVAL_MAX_EVENTS` (50,000), `ABB_EVAL_STALE_SECONDS` (3,600), `ABB_COMPARE_MAX_STEPS`
  (2,000), `ABB_JUDGE_PROVIDER` (`none`), `ABB_JUDGE_ALLOW_FAKE` (0), `ABB_JUDGE_COOLDOWN_SECONDS` (300). Evaluation jobs reuse the worker's lease
  and attempts; the handler's own timeout is shorter than the lease (validated in `Settings`: `eval_timeout_seconds < worker_lease_seconds`).
- **D14 Migrations 0070 and 0071, additive, no backfill.** **0070** `evaluations`: add `evaluator_id` (nullable composite FK to `evaluators`;
  `ON DELETE SET NULL` on a composite FK would null `workspace_id` too, so `RESTRICT`: evaluators are archived, never deleted), `kind` (CHECK, not null,
  server default `deterministic` for the empty table then default dropped), `implementation`, `state` (CHECK, default `pending`), `explanation`,
  `metrics` JSONB (default `{}`), `evidence_event_ids` JSONB (default `[]`), `config_snapshot` JSONB (default `{}`), `error_code`, `error`,
  `requested_by` (FK users, nullable), `started_at`, `completed_at`, `ground_truth` boolean (not null, default true; false for `llm_judge`), indexes
  `(workspace_id, run_id, evaluator_id, created_at DESC)` and `(workspace_id, state)` partial `WHERE state IN ('pending','running')`; CHECKs on
  `score BETWEEN 0 AND 1`, `pg_column_size(metrics) < 8192`, `pg_column_size(explanation) < 8192`; new table `evaluators`. **0071** `experiments`,
  `experiment_variants` (partial unique index `(workspace_id, experiment_id) WHERE role = 'baseline'`, unique `(workspace_id, experiment_id, name)`),
  `experiment_runs`. Both get `abb_runtime` DML through default privileges (asserted writable by `test_runtime_role.py` as every non-append-only
  table). `test_migrations.py`: the revision list gains `0070`, `0071`; a new `test_a_database_at_0047_upgrades_to_head_and_back_to_0047` mirrors
  the Phase 15 one. The ids 0070-0079 are reserved for this phase; see Merge notes for the `down_revision` rule.

### Action vocabulary additions (data in `authz/matrix.py`; the literal copy in `tests/authz/test_matrix_data.py` changes with it)

| Action | OWNER | ADMIN | DEVELOPER | VIEWER | SECURITY | BILLING | Key scope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `evaluation.read` (results: score, label, state; free text per D7) | x | x | x | x | x | - | `runs:read` |
| `evaluation.run` (start an evaluator on a run, evaluate an experiment) | x | x | x | - | - | - | - |
| `evaluation.label` (record a human verdict) | x | x | x | - | - | - | - |
| `evaluator.write` (define, archive) | x | x | x | - | - | - | - |
| `experiment.read` | x | x | x | x | x | - | `runs:read` |
| `experiment.write` (create, archive, variants, attach/detach runs) | x | x | x | - | - | - | - |

Comparison needs `run.read` only (it reads two runs the caller may already read; withholding per D6/D7). `GET /v1/evaluators` needs
`evaluation.read` (definitions are metadata; a judge rubric is visible to readers, documented: do not put secrets in rubrics).

## Proposed design

### API (`apps/api/src/abb_api/evaluations/`, `experiments/`, `comparison/`)

```text
evaluations/   schemas.py (EvaluatorIn/Out, EvaluationIn (trigger | human), EvaluationOut, pages), protocol.py (Evaluator Protocol, RunSnapshot,
               EvaluationResult), builtin.py (REGISTRY: name -> implementation; Config models), judge.py (JudgeModel, FakeJudgeModel, JudgeEvaluator),
               snapshot.py (build RunSnapshot from run row + events + spans + cost lines; bounded), repository.py (EvaluatorRepository,
               EvaluationRepository: insert pending, transition, latest-per-evaluator, page, counts for bounds), service.py (create/archive evaluators,
               trigger, label, list; withholding), router.py (GET/POST /v1/evaluators, DELETE /v1/evaluators/{evaluator_id},
               GET/POST /v1/runs/{run_id}/evaluations)
experiments/   schemas.py, repository.py (experiments, variants, runs, one metrics statement), service.py (bounds, control check, warnings,
               evaluate-all enqueue), router.py (GET/POST /v1/experiments, GET/DELETE /v1/experiments/{experiment_id},
               POST /v1/experiments/{experiment_id}/variants, POST /v1/experiments/{experiment_id}/runs,
               DELETE /v1/experiments/{experiment_id}/runs/{run_id}, POST /v1/experiments/{experiment_id}/evaluate)
comparison/    steps.py (step extraction, normalisation, keys: pure functions over Event lists), align.py (SequenceMatcher alignment, divergence,
               pure), service.py (loads both runs via RunService.visible_run, events, cost lines, latest evaluations; applies the actor's alignment
               mode), schemas.py (ComparisonOut), router.py (GET /v1/runs/{run_id}/compare)
jobs/handlers.py   EVALUATE_RUN = "evaluate_run" handler (D3), registered in HANDLERS
jobs/outbox.py     EVALUATE_RUN constant and `evaluate_key(workspace_id, evaluation_id)`
```

- 14 new operations (36 -> 50 in `test_openapi.py`; the literal surface set grows by the same 14). All take `X-ABB-Workspace` for people and
  accept keys where the matrix allows (reads only), so they fall in the `EITHER` security class except none; `main.py` gains three
  `include_router` lines.
- Error codes: `EVALUATOR_NOT_FOUND`, `EVALUATION_NOT_FOUND`, `EXPERIMENT_NOT_FOUND`, `VARIANT_NOT_FOUND` (all 404 with the uniform body for foreign and
  random ids), `EVALUATOR_ARCHIVED` (409), `EVALUATOR_KIND_MISMATCH` (422: a human label against a non-human evaluator and vice versa),
  `JUDGE_NOT_CONFIGURED` (409), `RUN_PROJECT_MISMATCH` (422), `BASELINE_EXISTS` (409), `LIMIT_REACHED` (409), `COMPARE_SAME_WORKSPACE` is not a
  code (a foreign `with` id is a plain 404).
- The repository layer takes `TenantContext` on every method (INV-3); the comparison service reuses `RunQueries` and `EventQueries` (no new SQL
  against `events` beyond `load_run`-style reads behind the store, INV-7). Project confinement: a project-bound key sees evaluators of its project
  or workspace-wide ones, experiments of its project, and compares runs of its project only (`RunQueries(conn, tenant, principal.project_id)` as today).
- Logging: structured, with `evaluation_id`, `evaluator`, `implementation`, `state`, `error_code`; never the explanation, rubric, prompt or reply.
  The existing log-hygiene test pattern is extended to the evaluation handler with the content-canary run.
- CLI: `evaluations-list --run <id>` (states and error codes, for operators) and nothing else; re-runs go through the API.

### Web

- `lib/routes.ts`: `comparePath(base, a, b)`, `experimentsPath(base)`, `experimentPath(base, id)`. `server/upstream.ts`: GET `evaluators`,
  `runs/{id}/evaluations`, `runs/{id}/compare`, `experiments`, `experiments/{id}`; POST `evaluators`, `runs/{id}/evaluations`, `experiments`,
  `experiments/{id}/(variants|runs|evaluate)`; DELETE `evaluators/{id}`, `experiments/{id}`, `experiments/{id}/runs/{id}` (same `ID` segment
  regex; `with` query parameter passes through the existing query allowlist by length). `lib/queries.ts`: `useEvaluators`, `useRunEvaluations`
  (polling while non-terminal), `useComparison`, `useExperiments`, `useExperiment`, mutations via `api.POST/DELETE` with query invalidation.
- Components: `Evaluations.tsx` (run page section), `VerdictForm.tsx`, `CompareView.tsx` + `AlignedSteps.tsx`, `Experiments.tsx`,
  `ExperimentDetail.tsx` (+ `VariantTable`, `ControlBanner`), `CompareSelect` inside `RunsTable`. Pages under `app/w/[workspace]/projects/[project]/
  {runs/compare, experiments, experiments/[experimentId]}`. `globals.css`: one block for the two-column step grid and the divergence marker
  (small, in its own commit). Accessibility: every state and relation has text; colour is never the only signal; the divergence announcement uses
  `aria-live="polite"`; axe runs in the Playwright spec on all three pages.
- Tests: vitest for `routes`, `upstream` additions, the hooks' polling stop condition, each component's states (loading, empty, withheld,
  truncated, not-controlled banner, judge badge), fixture replies; Playwright `evaluations.spec.ts` (real API) and an addition to `fixtures.spec.ts`.

### Migrations

Per D14; both reversible (`downgrade` drops the new tables and columns; the `evaluations` column drops restore the 0005 shape).

## Affected files

- API: new `evaluations/*`, `experiments/*`, `comparison/*`; `jobs/handlers.py` (+1 handler), `jobs/outbox.py` (+1 constant, +1 key helper),
  `authz/actions.py`, `authz/matrix.py`, `core/config.py` (D13), `cli.py` (`evaluations-list`), `db/tables.py` (+4 tables, +12 columns),
  `migrations/versions/0070_evaluations_and_evaluators.py`, `0071_experiments.py`, `main.py` (3 includes), `openapi.json` (regenerated), tests:
  new `test_evaluations_api.py`, `test_evaluators_builtin.py`, `test_evaluate_handler.py`, `test_judge.py`, `test_comparison_align.py`,
  `test_comparison_api.py`, `test_experiments_api.py`, `test_evaluation_isolation.py`; changed `authz/registry.py`, `authz/world.py` (seed an
  evaluator, an evaluation, an experiment with a variant in both tenants, canaries in names/descriptions/explanations/rubrics/configs),
  `authz/test_role_matrix.py` (14 rows), `authz/test_matrix_data.py`, `authz/test_cross_workspace.py` (`RANDOM_IDS` entries), `authz/
  test_content_withheld.py` (new routes), `test_openapi.py` (count and surface), `test_migrations.py`, `test_cli.py`, `test_config.py`.
- Web: `lib/{routes,queries}.ts`, `lib/api/{schema.d.ts (regenerated), types.ts}`, `server/upstream.ts`, `components/{Evaluations,VerdictForm,
  CompareView,AlignedSteps,Experiments,ExperimentDetail,RunsTable (select),RunDetail (section),AppShell (link)}.tsx`, `app/.../runs/compare/page.tsx`,
  `app/.../experiments/page.tsx`, `app/.../experiments/[experimentId]/page.tsx`, `fixtures/{evaluations.ts,api.ts}`, `app/globals.css`, tests and
  `e2e/evaluations.spec.ts`, `playwright.config.ts` (one project entry).
- Examples/scripts/CI: `examples/coding-agent/{run_demo.py,coding_agent/models.py,coding_agent/agent.py,tests/test_demo.py,README.md}`,
  `scripts/eval-e2e.sh`, `Makefile` (`eval-e2e`), `.github/workflows/ci.yml` (`eval-e2e` job).
- Docs: ADR-090/091/092 + `DECISIONS.md`, `architecture/api-v1.md` (new section "Evaluations, experiments, comparison"), `ARCHITECTURE.md`
  (module list, worker handlers), `SECURITY.md` (free text and judge content rules), `TESTING.md` (eval-e2e), `KNOWN_ISSUES.md` (new KIs: judge
  provider, auto-evaluation hook, `evaluations:write` scope, evaluation retention), `PROJECT_STATE.md`, `IMPLEMENTATION_PLAN.md` (Phase 11 line),
  `README.md` (one line in the feature list), `docs/screenshots/phase-11/`.

## Acceptance criteria (command-checkable)

- **AC-1 Contract and registry.** `cd apps/api && uv run pytest tests/authz -q` passes: every new route has a `RouteCase`, a literal `EXPECTED` row
  and the literal matrix equals the code matrix both ways; `tests/test_openapi.py` passes with `seen == 50` and the surface set listing the 14
  operations; `scripts/quality.sh full` exit 0 (openapi.json and `schema.d.ts` current, migrations 0070-0071 up/down/up, `next build`).
- **AC-2 Isolation (roadmap acceptance "evaluator failures never change run status").** `uv run pytest tests/test_evaluation_isolation.py -q`: an
  evaluator that raises, one that sleeps past `eval_timeout_seconds`, one that returns garbage, and a run above `eval_max_events` each yield one
  `failed` evaluation row with the expected `error_code`; the `runs` row (every column) and `summary` are byte-identical before and after; no
  `summarize_run` job was enqueued; the `evaluate_run` job is `done`, not `dead_letter`; the worker loop keeps processing other jobs; a database
  outage inside the handler retries the job (not recorded as an evaluation failure).
- **AC-3 Independent states and immutability.** `uv run pytest tests/test_evaluations_api.py -q`: trigger -> `pending` (202) -> after `drain()`
  `completed` with score/label/explanation/evidence; a second trigger creates a second row (the first unchanged); the 21st is `409 LIMIT_REACHED`;
  a human label is `completed` at insert and audited without its explanation; a label against a non-human evaluator is 422; a judge evaluator is
  `409 JUDGE_NOT_CONFIGURED` with `ABB_JUDGE_PROVIDER=none` and works with `fake` in test (`ground_truth: false`, `metadata.judge_model ==
  "fake-judge-1"`, `cost_usd == 0`); a stale pending row whose job is dead-lettered reads as `failed/JOB_FAILED` without a write.
- **AC-4 Built-ins are pure and content-free.** `uv run pytest tests/test_evaluators_builtin.py -q`: each built-in over fixture snapshots (pass,
  fail, unknown); same snapshot twice -> identical result; over the content-canary run no result field contains any `CONTENT_CANARIES` value;
  `evidence_event_ids` belong to the run (a foreign id is dropped and logged).
- **AC-5 Withholding.** `uv run pytest tests/authz/test_content_withheld.py tests/authz/test_cross_workspace.py -q`: for `viewer`, `billing` (where
  allowed) and keys without `payload.read`, no response of the 14 routes and the compare route contains a content canary; human/judge explanations
  and `metadata.raw` are null with `explanation_withheld: true`; the compare response says `alignment: "metadata"` and its labels carry
  `label_withheld`; files are counts with `paths_withheld: true`. Cross-workspace: foreign and random `evaluator_id`, `experiment_id`,
  `variant_id`, `run_id` and `with` give identical 404 bodies; no globex canary, id or magnitude in any body.
- **AC-6 Comparison (roadmap acceptance "two runs compared; divergence point identified").** `uv run pytest tests/test_comparison_align.py
  tests/test_comparison_api.py -q`: the D6 test list passes, including the demo pair (recorded offline with `ScriptedModel` both variants:
  divergence at the second `edit_file` step, `first_outcome_difference` at the second test run), reordered arrival, skew, renamed ids, inserted loop,
  truncation and symmetry.
- **AC-7 Bounded cost.** A test aligns two 2,000-step runs with disjoint and with shifted keys and asserts under 1.0 s each on the CI runner
  (`docs/benchmarks/phase-11-compare.md` records the local numbers; the test's threshold is 3 s to avoid flakiness, the benchmark the real figure);
  10,000-step runs are truncated and answered in the same budget; an experiment metrics statement over 500 runs x 20 evaluators runs under the
  analytics timeout on the seeded Stage A dataset (`scripts/analytics-e2e.sh` seed mode, numbers in the benchmark file).
- **AC-8 Experiments.** `uv run pytest tests/test_experiments_api.py -q`: create with one baseline and two candidates; a second baseline is 409; a
  run from another project is 422; attaching the 501st run is 409; `controlled` and `changed_variables` are right for configs differing in 0, 1 and
  2 keys (missing key counts); `variant_models_vary` fires when runs' `summary.models` differ; metrics (success rate, mean/median/p95 cost and
  latency, retry rate, tool calls, failure categories, per-evaluator mean/pass rate) equal a Python recomputation over the seeded runs;
  `evaluate` enqueues `runs x evaluators` minus completed, idempotent on repeat (0 new jobs), capped at 2,000.
- **AC-9 Web.** `pnpm --filter @abb/web test` passes with the new component and hook tests; `make eval-e2e` passes: Playwright sees both
  evaluations on the run page (judge badge text "not ground truth"), records a verdict as OWNER, is refused the form as VIEWER (no form, results
  visible, explanation withheld text), the experiment page shows the not-controlled banner naming both keys and the metrics table, the compare page
  marks the divergence row and "Jump to divergence" scrolls to it; axe clean on the three pages; screenshots in `docs/screenshots/phase-11/`.
- **AC-10 Compatibility.** `make smoke`, `make sdk-e2e`, `make coding-e2e`, `make auth-e2e`, `make stream-e2e`, `make analytics-e2e` pass unchanged;
  `examples/coding-agent` tests pass for both variants; `uv run pytest tests/test_runtime_role.py -q` proves the four new tables are writable by
  `abb_runtime` and `APPEND_ONLY_TABLES` is unchanged.
- **AC-11 Security review.** A `review-change` pass with the attack list below; findings fixed or filed as KIs.

## Verification plan

1. Per step: focused pytest/vitest, then `scripts/quality.sh quick`; `full` before each commit; `make eval-e2e` from step 11 on; the other E2E
   scripts at steps 9 and 13 (proxy and nav change).
2. Mutation checks during review (each must fail at least one test): remove the `try/except` around the evaluator call in the handler; let the handler
   call `RunRepository.apply_derivation`; drop `holds(PAYLOAD_READ)` in the comparison service; include `shell.command` in the metadata alignment key;
   remove the workspace predicate from `EvaluationRepository.latest_per_evaluator`; grant `evaluation.run` to VIEWER in `matrix.py` only; drop the
   partial unique index on baseline; remove the 20-rows bound; skip the `evidence_event_ids` membership check; return `metadata.raw` to a viewer.
3. Browser: Playwright plus one manual pass through `make dev` with the fixture user and with the fake provider session.
4. Environment: step 1 installs dependencies and runs `scripts/quality.sh quick`; items that cannot run here are recorded `UNVERIFIED (env)` with
   the command that would verify them. No acceptance criterion depends on a vendor key.

## Security review note (what an adversarial reviewer should attack)

1. **Content through side doors.** Evaluator explanations, `metrics`, `evidence` labels, judge `metadata.raw`, variant `config`, experiment
   names/descriptions, evaluator `config` (`allowed_globs`, `required_paths` are paths: readable by anyone with `evaluation.read`; document that
   evaluator configs are workspace metadata and must not hold secrets; a VIEWER can read them: decide if that is acceptable, default yes, since they
   are policy, not telemetry), the compare response's labels, keys (never returned), files and `cost_by_model` for a VIEWER; the metadata alignment
   as an equality oracle (D6 removes content from keys; verify `llm.model` in keys is acceptable: it is in `summary.models`, visible to VIEWER today).
2. **Run status integrity.** Any write path from the evaluation handler or services to `runs`, `spans`, `events`, `cost_calculations`, `outbox_jobs`
   of type `summarize_run`; an evaluator that enqueues jobs; a judge provider exception; a handler timeout longer than the lease (heartbeat masks it;
   `Settings` validation forbids it).
3. **IDOR.** `with=` run id of another workspace or another project (project-bound key); `evaluator_id` of another workspace in a trigger
   (404, identical body); `run_id` of another project attached to an experiment (422 by project, 404 by workspace); `variant_id` of another
   experiment (404); evaluations listed by a foreign run id (404).
4. **Floods and cost.** 1,000 triggers on one run (20 rows then 409; jobs coalesce per evaluation id, so 20 jobs at most); `evaluate` on a 500 x 20
   experiment (2,000 cap, idempotent); judge budget (`max_cost_usd`, cooldown); `include_content` prompts bounded by `max_input_chars`; huge runs
   (`eval_max_events`, `compare_max_steps`); a 10 MB explanation (422 before the database).
5. **Privilege.** DEVELOPER archiving an evaluator they did not create (allowed by the matrix: `evaluator.write` has no `Own`; confirm intended),
   VIEWER triggering/labelling (403, audited within the bucket), keys posting labels (403 `INSUFFICIENT_SCOPE` with no `required_scope` since no
   scope grants it: confirm the message reads well), BILLING reading evaluations (403).
6. **Fake judge in production.** `ABB_JUDGE_PROVIDER=fake` with `ABB_ENVIRONMENT=production` and no `ABB_JUDGE_ALLOW_FAKE` (startup refuses with a
   clear error, not a warning); results of a fake judge carry `metadata.provider = "fake"` so they can never be mistaken for a model's.
7. **Logs and audit.** No explanation, rubric, prompt, reply, command or path in any log line or audit `details` (hygiene test over the canary run).
8. **Determinism claims.** Same inputs twice give byte-identical comparison and evaluation results (ordering of dict keys, float formatting).

## Risks

1. **Merge conflicts with Phases 9, 10 and 16** (shared files listed in Merge notes). Mitigation: hot-file edits isolated in their own small commits;
   generated artifacts regenerated in the last commit of each step; the migration `down_revision` rule below.
2. **Alignment quality on real traces** (normalisation too weak: everything `changed`; too strong: unrelated steps `equal`). Mitigation: the D6 test
   list uses the real demo events; the normaliser is a pure function with a table-driven test; `alignment` and `truncated` are explicit in the
   response so the UI never implies more than the algorithm knows.
3. **Judge semantics without a real provider.** The fake proves plumbing, budgets and labelling, not judgement quality; the ADR says so and the UI
   labels every judge result. Mitigation: provider interface is small (one method) and the KI names the manual check.
4. **Worker regressions.** A new handler in the only worker. Mitigation: the handler's exceptions are contained (AC-2); its timeout is below the lease;
   `test_outbox_worker.py` runs unchanged.
5. **UI scope.** Three new pages and a run-page section. Mitigation: components are presentational over API shapes; fixtures power most tests; the
   compare page reuses the timeline's virtualisation and drawer.
6. **Experiment metrics drift from analytics** (two percentile implementations). Mitigation: experiments use exact `percentile_cont` over at most 500
   rows and the docs say analytics percentiles are histogram-based (KI-053); tests compare to a Python recomputation.
7. **Schema evolution of the skeleton `evaluations` table.** Adding NOT NULL columns to an existing (empty everywhere, but not provably) table.
   Mitigation: server defaults for every new NOT NULL column, dropped afterwards only where a default is wrong; migration tested up/down/up and from 0047.
8. **Viewer alignment differs from a developer's** (decision 5): two people may see different divergence points. Mitigation: the response and the page
   state the alignment mode; documented in `api-v1.md`.

## Merge notes (parallel phases; keep these edits small and in dedicated commits)

- **Migrations.** This phase uses 0070 and 0071 with `down_revision = "0047"` in 0070. If another phase merges first and `0047` is no longer the
  head on `main`, rebase and change **only** 0070's `down_revision` to the new head (the ids stay 0070/0071, as Phase 6 renumbered 0030 -> 0044 when
  it merged after Phases 7 and 8), update the expected revision list in `test_migrations.py`, and re-run the migration cycle. The later-merging phase
  always makes this edit; never renumber another phase's files.
- **Hot files this phase touches and how:** `apps/api/src/abb_api/main.py` (+3 `include_router` lines, step 4/7/8 commits), `apps/api/openapi.json`
  and `apps/web/src/lib/api/schema.d.ts` (regenerated in the last commit of steps 4, 7, 8), `authz/actions.py` + `authz/matrix.py` +
  `tests/authz/test_matrix_data.py` + `tests/authz/registry.py` + `tests/authz/test_role_matrix.py` + `tests/authz/test_cross_workspace.py` (step 3
  adds all six actions and `RANDOM_IDS` at once; later steps add only `RouteCase`s and `EXPECTED` rows), `tests/test_openapi.py` (count 36 -> 50 and
  the surface set, one edit per router step), `tests/test_migrations.py` (step 2), `jobs/handlers.py` + `jobs/outbox.py` (step 5; Phase 10 will add
  detector handlers next to it: additive lines, trivial to merge), `apps/web/src/components/AppShell.tsx` (one link, step 13), `apps/web/src/app/
  globals.css` (one appended block, step 12), `apps/web/src/server/upstream.ts` (three regex lines, step 9), `Makefile` + `.github/workflows/ci.yml`
  (one target and one job, step 14), `README.md`, `docs/KNOWN_ISSUES.md`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md`,
  `ARCHITECTURE.md` (step 15 only). Not touched: `runs/summary.py`, `runs/router.py`, `streaming/*`, `analytics/*`, `core/logging.py`, `proxy.ts`.
- **Shared conventions with Phase 10.** Both phases store `evidence_event_ids` as a JSONB array of `evt_` ids validated against the run; both
  version their producers (`evaluator_version` / `detector_version`); neither hooks `summarize_run` in this round (decision 2). If Phase 10 lands a
  `findings` table first, this phase does not reuse it (different lifecycle and states).

## Ordered steps (one commit each; `scripts/quality.sh full` green after every step)

1. `chore(phase-11): install dependencies, record environment, add settings and action vocabulary` - `pnpm install`, `uv sync`, `quality.sh quick`
   evidence in this plan; `core/config.py` D13 settings with the lease validation; `authz/actions.py` six actions (granted to nobody yet, like the
   reserved ones; `ALL_ACTIONS` updated); `test_config.py`.
2. `feat(db): migrations 0070 (evaluators, evaluation columns) and 0071 (experiments); tables.py; migration tests` - revision list, the 0047 -> head test,
   runtime-role writability.
3. `feat(authz): grants for evaluation, evaluator and experiment actions; registry id parameters` - `matrix.py` and the literal copy; `RANDOM_IDS`
   for the new id kinds; world seeds (evaluator, evaluation, experiment, variant in both tenants with canaries). No routes yet, so the registry stays
   complete.
4. `feat(evaluations): evaluator protocol, built-ins, snapshot, repositories, evaluator endpoints` - `GET/POST /v1/evaluators`, `DELETE
   /v1/evaluators/{id}`; `RouteCase`s, `EXPECTED` rows, `test_openapi` (+3), `test_evaluators_builtin.py`; openapi.json and schema.d.ts regenerated last.
5. `feat(jobs): evaluate_run handler with timeout, event cap and contained failures` - `EVALUATE_RUN`, `test_evaluate_handler.py`,
   `test_evaluation_isolation.py` (AC-2), log hygiene.
6. `feat(evaluations): trigger and human-label endpoints, listing with withholding, stale-pending derivation` - `GET/POST /v1/runs/{run_id}/evaluations`;
   bounds; audit of labels; `test_evaluations_api.py`; `test_content_withheld.py` extended; registry/openapi (+2); regenerated artifacts last.
7. `feat(evaluations): model-judge interface, deterministic fake, budgets and labelling` - `judge.py`, `ABB_JUDGE_*`, startup refusal, `test_judge.py`.
8. `feat(comparison): step extraction, normalisation, alignment and GET /v1/runs/{run_id}/compare` - pure modules first (`test_comparison_align.py`,
   including the recorded demo pair as a fixture file under `tests/fixtures/`), then the service/router with role-aware alignment
   (`test_comparison_api.py`), registry/openapi (+1), benchmark numbers in `docs/benchmarks/phase-11-compare.md` (AC-7); regenerated artifacts last.
9. `feat(experiments): tables' repositories, control check, metrics, endpoints, evaluate-all` - 8 routes, bounds, `test_experiments_api.py`,
   registry/openapi (+8), regenerated artifacts last.
10. `feat(example): coding-agent --variant wrong-fix; demo tests for both stories` - `models.py`, `agent.py` (error completion), `run_demo.py`, README.
11. `feat(web): proxy routes, client hooks, fixtures for evaluations, comparison and experiments` - `upstream.ts`, `queries.ts`, `routes.ts`,
    `fixtures/evaluations.ts` + `api.ts`, vitest.
12. `feat(web): run-page evaluations section, verdict form, judge badge; compare selection in the runs list` - components, tests, `globals.css` block.
13. `feat(web): compare page with aligned steps and divergence; experiments list and detail with the control banner; nav link` - pages, components,
    `AppShell` link, tests.
14. `chore(e2e): scripts/eval-e2e.sh, Playwright evaluations.spec.ts, make eval-e2e, CI job, screenshots` - runs every E2E script once.
15. `docs(phase-11): ADR-090/091/092, DECISIONS, api-v1, ARCHITECTURE, SECURITY, TESTING, KNOWN_ISSUES (new KIs), PROJECT_STATE, roadmap, README` -
    then `complete-phase` with the AC evidence recorded in this file.

## Appendix: ADR drafts (written at step 15 as `docs/decisions/ADR-090..092`; the files are authoritative)

### ADR-090: Evaluations are derived, versioned records with independent state, computed by the outbox worker

**Context.** Spec §85 asks for evaluators with their own states whose failures never change a run; INV-1/INV-2 forbid editing events and require
derived state to be rebuildable; the `evaluations` skeleton table exists since migration 0005; the worker and outbox (ADR-012) already give
at-least-once, leased, bounded background execution.

**Decision.** An evaluation is a row produced by applying one versioned evaluator definition (with a configuration snapshot) to a run's events; a
re-run is a new row and a terminal row is never updated; human verdicts are rows of kind `human` completed at insert. Evaluators are workspace
definitions of built-in implementations (deterministic, metric, human, model judge) configured by validated JSON; no user code runs in the
worker. Triggering inserts a `pending` row and an `evaluate_run` job in one transaction; the handler moves the row to `running`, builds a bounded
`RunSnapshot` (`ABB_EVAL_MAX_EVENTS`), runs the implementation under `ABB_EVAL_TIMEOUT_SECONDS` (shorter than the lease), and records
`completed` or `failed` with an error code. Evaluator exceptions, timeouts and oversized runs are results, not job failures; only infrastructure
errors retry. The handler never writes run, span, event or cost tables. Pending rows whose job died read as `failed` without a write. Results carry
`score`, `label`, `explanation`, `metrics`, `evidence_event_ids` (validated to the run). Free-text explanations of human and judge results need
`payload.read`; built-in explanations are templates over numbers. Bounds: 100 evaluators per workspace, 20 rows per (run, evaluator), 50 human rows
per run. Automatic evaluation on run completion is deferred (it hooks the summarizer, shared with Phase 10's detectors).

**Alternatives.** Evaluate inside `summarize_run` (couples run derivation to evaluator cost and failures; forbidden by §85.2). Store results as
events (they are not telemetry; different author, lifecycle and permissions). A plugin loader for user evaluators (arbitrary code in the worker;
later, behind an ADR). An append-only table (state transitions need UPDATE; terminal immutability is a service rule with a test).

**Consequences.** Results are reproducible from (events, evaluator version, config); the worker gains one bounded handler; evaluation history
grows per re-run up to the bound (retention is Phase 19); people trigger evaluations, SDK keys cannot until a scope is added (KI).

### ADR-091: Model judges behind a one-method provider interface; a deterministic fake is the only shipped provider; judges are never ground truth

**Context.** Spec §85.4: capture judge model, prompt template version, cost and raw score metadata; judges are not ground truth. No vendor
credentials exist in this project; INV-5 forbids sending captured content anywhere by default; the API must not depend on a vendor SDK.

**Decision.** `JudgeModel.complete(prompt, max_output_tokens) -> JudgeReply(text, model, tokens, cost)` is the whole provider contract. The
`judge` implementation builds a prompt from a rubric and a run projection that, unless `include_content: true`, equals what a VIEWER sees (no
command text, paths or payloads), bounded by `max_input_chars`, and records `judge_model`, `prompt_template_version`, tokens, cost, provider and a
truncated raw reply. Budgets: `max_cost_usd` per evaluation and a cooldown after an overrun. `ABB_JUDGE_PROVIDER` is `none` (judge evaluators cannot
be created) or `fake` (a deterministic keyword scorer, `fake-judge-1`, cost 0), the latter refused in production unless `ABB_JUDGE_ALLOW_FAKE=1`.
Every judge result is `kind=llm_judge`, `ground_truth=false`, and the UI labels it "Model judge - not ground truth" wherever a score appears. A real
provider (Anthropic Messages over `httpx`) is an adapter implementing the same method, added only with the user's key and one manual run (KI).

**Alternatives.** Ship a vendor client now (unverifiable here, a dependency with a key path, and INV-5 exposure by default). Treat judge scores like
deterministic ones (the spec forbids it). Run judges in the API request (unbounded latency and cost on the request path).

**Consequences.** Plumbing, budgets, labelling and withholding are tested end to end without any vendor; judgement quality is unproven until a
provider exists; content reaches a provider only by explicit per-evaluator opt-in.

### ADR-092: Run comparison by normalised step alignment, computed on read, bounded and role-aware

**Context.** Spec §98: comparison must identify divergence, align semantically equivalent actions and normalise generated ids and timestamps;
§86 metrics; INV-7 (no ad-hoc SQL against events); ADR-061 (`payload.read` withholds command text and paths; a comparison must not become an
equality oracle on them).

**Decision.** `GET /v1/runs/{id}/compare?with=` derives a step list per run from events in canonical order (closing lifecycle events and point
events of the file, git, reliability, security and approval families), gives each step a key `(family, normalised name)` where the normaliser
replaces ULIDs, UUIDs, long hex, timestamps, durations and digit runs with `#`, strips temp and workdir prefixes, collapses whitespace and
lower-cases, and aligns the two key sequences with `difflib.SequenceMatcher(autojunk=False)` over at most `ABB_COMPARE_MAX_STEPS` steps per side
(`truncated` beyond). Status is excluded from keys so the same action with a different outcome aligns as `changed`. The response holds the
relations, the first structural divergence, the first outcome difference, summary deltas, cost by model, file-set differences and the latest
evaluation scores per evaluator; nothing is stored. Actors without `payload.read` get keys and labels built from kinds, tool names and model
names only (`alignment: metadata`), with paths and commands absent; the alignment mode is stated in the response and on the page.

**Alternatives.** Store comparisons (derived, cheap, and role-dependent: nothing to gain). Align by timestamp (skew and nondeterministic
durations make it wrong). A custom LCS (the stdlib matcher is deterministic and adequate at the cap; measured in `docs/benchmarks/phase-11-compare.md`;
a plain LCS is the fallback if it is not). Full event-level diffs (noise; the step level is what §98's "aligned trace steps" means; events show in
the drawer).

**Consequences.** Any two visible runs compare in one bounded request; divergence points can differ by role, by design; very long runs are
compared only up to the cap, which the response and UI state.
