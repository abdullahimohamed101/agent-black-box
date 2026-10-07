.PHONY: audit setup dev db db-stop migrate test lint format typecheck quality quality-full up down clean help

API := apps/api
SCHEMA := packages/event-schema
ENV_FILE := .env

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

# One-time (and repeatable) developer setup: .env, Python env, JS deps, database.
setup:
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	cd $(API) && uv sync
	cd $(SCHEMA) && uv sync
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
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && uv run pytest -q
	pnpm --filter @abb/web test

lint:
	cd $(SCHEMA) && uv run ruff check . && uv run ruff format --check .
	cd $(API) && uv run ruff check . && uv run ruff format --check .
	pnpm --filter @abb/web lint && pnpm --filter @abb/web format:check

format:
	cd $(SCHEMA) && uv run ruff check --fix . && uv run ruff format .
	cd $(API) && uv run ruff check --fix . && uv run ruff format .
	pnpm --filter @abb/web format

typecheck:
	cd $(SCHEMA) && uv run mypy
	cd $(API) && uv run mypy
	pnpm --filter @abb/web typecheck

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
