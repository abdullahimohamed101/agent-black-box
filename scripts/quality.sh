#!/usr/bin/env bash
# Single quality gate used by developers, agents and CI.
#   quick: lint + typecheck + tests         full: quick + migration cycle + builds
set -euo pipefail
mode="${1:-quick}"
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${ABB_DATABASE_URL:?ABB_DATABASE_URL is not set; copy .env.example to .env}"

step() { printf '\n==> %s\n' "$*"; }

step "schema: ruff / mypy / pytest"; (cd packages/event-schema && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q)
step "sdk-python: ruff / mypy / pytest (coverage gate)"; (cd packages/sdk-python && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q --cov --cov-report=term:skip-covered)
for pkg in conformance langgraph openai anthropic mcp; do   # framework adapters: separate environments (ADR-050)
  step "integrations/$pkg: ruff / mypy / pytest (offline)"
  (cd "integrations/$pkg" && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q --cov --cov-report=term:skip-covered)
done
step "schema: generated artifacts are current"; make -s schema-check
step "api: openapi.json is current"; (cd apps/api && uv run python -m abb_api.openapi --check)
step "api: ruff check / format";   (cd apps/api && uv run ruff check . && uv run ruff format --check .)
step "api: mypy";                  (cd apps/api && uv run mypy)
step "web: generated API client is current"; pnpm --filter @abb/web gen:api:check
step "web: eslint / prettier";     pnpm --filter @abb/web lint && pnpm --filter @abb/web format:check
step "web: tsc";                   pnpm --filter @abb/web typecheck
step "api: pytest";                (cd apps/api && uv run pytest -q)
step "web: vitest";                pnpm --filter @abb/web test

if [ "$mode" = "full" ]; then
  step "api: migrations up/down/up on the test database (never the dev database)"
  : "${ABB_TEST_DATABASE_URL:?ABB_TEST_DATABASE_URL is not set}"
  (cd apps/api && export ABB_DATABASE_URL="$ABB_TEST_DATABASE_URL" \
    && uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head)
  step "web: next build";          pnpm --filter @abb/web build
  step "api: package build";       (cd apps/api && uv build --wheel >/dev/null)
fi
printf '\nquality (%s): OK\n' "$mode"
