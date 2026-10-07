# Phase 0 - Repository and Engineering Foundation

Status: Completed on branch `feature/phase-0-foundation`; criterion 7 (CI run) is UNVERIFIED (env): no GitHub remote exists (KI-007)
Owner: coding agent
Depends on: none
Spec: §57, §103, §106, §108, §111, §128-129, §131, §133-134, §145 (items 1-2, 5), ADR-003

## Outcome

Clone, `cp .env.example .env`, `make setup && make dev`: the web shell loads and shows API
health, `/healthz` and `/readyz` respond, PostgreSQL is reachable and migrated, and
`make test lint typecheck` pass locally and in CI. No product features yet.

## Non-Goals

No event schema (Phase 1), no domain tables beyond the Alembic baseline (Phase 2), no SDK,
no auth, no UI beyond a shell. No Kafka/ClickHouse/Redis/Kubernetes/Terraform directories.

## Current Architecture

Empty. Docs only.

## Decisions (confirm before implementation)

- **D1 Python 3.12**, venv + `uv` if installable else `pip` + `pip-tools` lockfiles; one
  `pyproject.toml` per Python package (`apps/api`, later `packages/*`), a root Makefile.
- **D2 pnpm workspace** for `apps/web` and later TS packages; Next.js App Router, TypeScript strict.
- **D3 SQLAlchemy 2 (async engine, asyncpg) + Alembic**; Postgres 16; config via `pydantic-settings`
  validated at startup (fail fast on missing required settings).
- **D4 App factory pattern** (`create_app(settings)`), modules directory `apps/api/src/abb_api/{core,ingestion,runs,...}`
  created only when used; Phase 0 contains `core` (config, logging, errors, request-id) and `health`.
- **D5 Structured JSON logging** (stdlib + a small formatter) with `request_id`; no third-party logger.
- **D6 Quality gate script** `scripts/quality.sh quick|full` is the single entry used by Makefile, CI and agents.
- **D7 Compose runs Postgres (+ api, web profiles)**; the API/web also run natively against it.
- **D8 CI**: GitHub Actions, Postgres service container, jobs: api (ruff, mypy, pytest, alembic up/down/up),
  web (eslint, tsc, vitest, build). Same commands as `quality.sh`.
- **D9 Typed error envelope and error taxonomy skeleton** (spec §101.2, §130) shipped now so later phases extend it.

## Proposed Design (layout)

```text
Makefile  scripts/quality.sh  .env.example  docker-compose.yml  .github/workflows/ci.yml
apps/api/   pyproject.toml  alembic.ini  migrations/  src/abb_api/{main.py,core/,health/}  tests/
apps/web/   package.json  src/app/{layout,page}.tsx  src/lib/api.ts  tests/
```

Endpoints: `GET /healthz` (liveness, no deps), `GET /readyz` (DB `SELECT 1`, 503 + typed error if down).
Headers: `X-Request-ID` accepted/generated and echoed; included in every log line.

## Acceptance Criteria (commands)

1. `make setup` completes on a clean checkout (venv, deps, pnpm install).
2. `make lint`, `make typecheck`, `make test` exit 0; test counts recorded.
3. `make migrate` applies the baseline to an empty database; `alembic downgrade base && upgrade head` repeats cleanly.
4. `make dev` starts API + web; `curl -s localhost:8000/healthz` -> 200; `/readyz` -> 200; with Postgres stopped -> 503 JSON error with `request_id`.
5. Browser: `localhost:3000` renders the shell and shows API health (loading, ok, and error states exercised).
6. `docker compose up` starts Postgres, api and web healthy; `docker compose ps` shows healthy.
7. CI workflow passes on a pull request (or `act`/local equivalent recorded as UNVERIFIED if no remote).
8. Docs: README quickstart accurate; `PROJECT_STATE.md` updated; ADR-003 written.

### Evidence (2026-10-07)
1. PASS `make setup` in a fresh `git clone` (new venv, `pnpm install`, Postgres up, migrations applied).
2. PASS `make test lint typecheck`: pytest 10 passed (real Postgres), vitest 6 passed, ruff/mypy/eslint/prettier/tsc clean.
   `scripts/quality.sh full` OK (adds migration up/down/up on `abb_test`, `next build`, wheel build).
3. PASS `alembic downgrade base && upgrade head` cycle (also asserted in `tests/test_migrations.py`).
4. PASS `make dev` from the fresh clone: `/healthz` 200, `/readyz` 200 with request ID header and JSON log lines; with Postgres
   stopped `/readyz` -> 503 `DEPENDENCY_UNAVAILABLE` (retryable, request_id, no internals); recovered after restart.
   Missing `ABB_DATABASE_URL` fails startup naming `database_url`.
5. PASS browser (built-in browser): `localhost:3000` shows loading -> "Ready (API v0.0.0, database connected)"; error state with
   request ID with Postgres stopped; "API unreachable" with API stopped; console clean.
6. PASS `docker compose --profile app up -d --build --wait`: postgres, api, web all healthy; containers run as uid 10001.
7. UNVERIFIED (env) CI workflow has not run (no remote). The same steps ran locally.
8. PASS README quickstart matches what was run; ADR-003 written; PROJECT_STATE updated.

## Verification Plan

Run each command on a fresh clone in a temp directory; capture output into the completion report;
exercise the failure paths (DB down, bad config, missing env var -> startup error naming the variable).

## Risks

- Tooling not installed (above). - Next.js/Node version churn: pin via `.nvmrc`/`packageManager`.
- Over-building the skeleton: nothing beyond the listed files.

## Ordered Steps

1. [x] `git init`, initial commit of the docs; branch `feature/phase-0-foundation`.
2. [x] Root tooling: Makefile, `scripts/quality.sh`, `.env.example`, editorconfig, CI skeleton.
3. [x] `apps/api`: pyproject, app factory, config, logging, request-id, typed errors, `/healthz` + tests.
4. [x] DB: async engine, session dependency, Alembic baseline, `/readyz` + repository-style tests on real Postgres.
5. [x] `docker-compose.yml` (Postgres, api profile) and API Dockerfile (multi-stage, non-root, healthcheck).
6. [x] `apps/web`: Next.js shell, health display, vitest + one component test, Dockerfile.
7. [x] CI workflow matching `quality.sh`; verify locally.
8. [x] Docs: README quickstart, setup.md, TESTING.md commands made real, ADR-003.
9. [x] `verify-change`, `review-change`, `complete-phase`; move plan to `completed/`.
