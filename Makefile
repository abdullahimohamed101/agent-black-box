INTEGRATIONS := conformance langgraph openai anthropic mcp
.PHONY: integrations-test integrations-e2e stream-e2e sdk-e2e e2e e2e-real web-client openapi openapi-check smoke bench seed schema schema-check audit setup dev db db-stop migrate test lint format typecheck quality quality-full up down clean help analytics-e2e analytics-bench-seed analytics-bench coding-e2e

API := apps/api
SCHEMA := packages/event-schema
SDK := packages/sdk-python
ENV_FILE := .env

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

# One-time (and repeatable) developer setup: .env, Python env, JS deps, database.
setup:
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	cd $(API) && uv sync
	cd $(SCHEMA) && uv sync
	cd $(SDK) && uv sync
	pnpm install --frozen-lockfile
	$(MAKE) db
	$(MAKE) migrate

# Postgres only (host port 5433). Waits until healthy.
db:
	docker compose up -d --wait postgres

db-stop:
	docker compose stop postgres

migrate:
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && uv run alembic upgrade head

# API on :8000 and web on :3000 natively against the compose database. Ctrl-C stops both.
dev: db
	@set -a && . ./$(ENV_FILE) && set +a; \
	trap 'kill 0' INT TERM; \
	(cd $(API) && uv run uvicorn abb_api.main:app_from_env --factory --reload --port 8000) & \
	(pnpm --filter @abb/web dev) & \
	wait

test:
	cd $(SCHEMA) && uv run pytest -q
	cd $(SDK) && uv run pytest -q
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && uv run pytest -q
	pnpm --filter @abb/web test

lint:
	cd $(SCHEMA) && uv run ruff check . && uv run ruff format --check .
	cd $(SDK) && uv run ruff check . && uv run ruff format --check .
	cd $(API) && uv run ruff check . && uv run ruff format --check .
	pnpm --filter @abb/web lint && pnpm --filter @abb/web format:check

format:
	cd $(SCHEMA) && uv run ruff check --fix . && uv run ruff format .
	cd $(SDK) && uv run ruff check --fix . && uv run ruff format .
	cd $(API) && uv run ruff check --fix . && uv run ruff format .
	pnpm --filter @abb/web format

typecheck:
	cd $(SCHEMA) && uv run mypy
	cd $(SDK) && uv run mypy
	cd $(API) && uv run mypy
	pnpm --filter @abb/web typecheck

# Local development only: workspace "local", project "demo" and a dev API key in .local/dev-api-key
# (owner-only file, gitignored). Safe to repeat; it keeps a still-valid key.
seed:
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && ABB_ALLOW_DEV_SESSIONS=1 uv run python -m abb_api.cli seed --key-file $(CURDIR)/.local/dev-api-key

# Regenerate the committed JSON Schema and TypeScript types from the Pydantic models.
schema:
	cd $(SCHEMA) && uv run python -m abb_event_schema.export
	pnpm --filter @abb/event-schema generate

# Fails if the committed schema/types differ from what the models generate.
schema-check:
	cd $(SCHEMA) && uv run python -m abb_event_schema.export --check
	pnpm --filter @abb/event-schema generate
	git diff --exit-code -- $(SCHEMA)/ts $(SCHEMA)/schemas

# The committed API contract (apps/api/openapi.json), generated from the FastAPI app.
openapi:
	cd $(API) && uv run python -m abb_api.openapi

openapi-check:
	cd $(API) && uv run python -m abb_api.openapi --check

# End-to-end check of the running container stack (run `make up` first).
smoke:
	scripts/smoke.sh

# Ingestion and read latency against the running stack (needs `make up` and `make seed`).
bench:
	cd $(API) && uv run python ../../scripts/bench_ingest.py --key-file $(CURDIR)/.local/dev-api-key

# SDK end to end against the running stack (needs `make up`).
sdk-e2e:
	scripts/sdk-e2e.sh

# Known-vulnerability scan (spec §111). JS: production dependencies only; dev-only findings are
# tracked in docs/KNOWN_ISSUES.md. Python: whole locked set.
audit:
	pnpm audit --prod
	cd $(API) && uv export --frozen --no-hashes --no-emit-project -o /tmp/abb-api-requirements.txt >/dev/null && uvx --python 3.12 pip-audit -r /tmp/abb-api-requirements.txt

quality:
	scripts/quality.sh quick

quality-full:
	scripts/quality.sh full

# Full stack in containers (api + web + postgres).
up:
	docker compose --profile app up -d --build --wait

down:
	docker compose --profile app down

# Web: typed client from the committed OpenAPI contract; Playwright on fixtures (port 3100) and on a real ingested run.
web-client:
	pnpm --filter @abb/web gen:api

e2e:
	pnpm --filter @abb/web build && pnpm --filter @abb/web test:e2e

stream-e2e:
	pnpm --filter @abb/web build && scripts/stream-e2e.sh

coding-e2e:
	pnpm --filter @abb/web build && scripts/coding-e2e.sh

e2e-real:
	pnpm --filter @abb/web build && scripts/e2e-web-real.sh

# Analytics: browser E2E over known runs, and the Stage A benchmark (docs/benchmarks/phase-7-analytics.md).
analytics-e2e:
	pnpm --filter @abb/web build && scripts/analytics-e2e.sh

analytics-bench-seed:
	ANALYTICS_E2E_MODE=seed scripts/analytics-e2e.sh

analytics-bench:
	ANALYTICS_E2E_MODE=bench scripts/analytics-e2e.sh

# Framework adapters (ADR-050): each has its own environment; all tests run offline.
integrations-test:
	for p in $(INTEGRATIONS); do (cd integrations/$$p && uv run pytest -q) || exit 1; done

integrations-e2e:
	scripts/integrations-e2e.sh
