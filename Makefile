INTEGRATIONS := conformance langgraph openai anthropic mcp
.PHONY: observability-token observability-up observability-down integrations-test integrations-e2e stream-e2e sdk-e2e e2e e2e-real web-client openapi openapi-check smoke bench seed schema schema-check audit audit-pnpm audit-lockfiles check-pins setup dev db db-stop migrate test lint format typecheck quality quality-full up down clean help analytics-e2e analytics-bench-seed analytics-bench coding-e2e demo demo-reset demo-e2e site site-check auth-e2e auth-https-e2e multiagent-e2e findings-e2e eval-e2e security-e2e policy-e2e alerts-e2e hardening-e2e obs-e2e retention-e2e backup-restore-e2e hosting-e2e hosting-verify backup restore backup-verify rotate-alert-keys

API := apps/api
SCHEMA := packages/event-schema
SDK := packages/sdk-python
# The web's copy of the event-type registry (span roles and kinds for lib/spans.ts, Phase 9 D5).
WEB_GENERATED := apps/web/src/lib/generated
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

# Backup and restore (ADR-162; docs/runbooks/backup-restore.md). OUT and BUNDLE are absolute paths.
#   make backup OUT=/var/backups/abb/2026-10-16 [BACKUP_FLAGS="--encrypt --passphrase-file /etc/abb/backup.pass"]
#   make backup-verify BUNDLE=/var/backups/abb/2026-10-16 [BACKUP_FLAGS="--passphrase-file ..."]
#   make restore BUNDLE=/var/backups/abb/2026-10-16 ARTIFACT_DIR=/new/empty/dir [BACKUP_FLAGS=...]  (needs ABB_RESTORE_DATABASE_URL)
backup:
	@test -n "$(OUT)" || { echo "usage: make backup OUT=<new directory>"; exit 2; }
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && uv run python -m abb_api.backup create --out "$(OUT)" $(BACKUP_FLAGS)

backup-verify:
	@test -n "$(BUNDLE)" || { echo "usage: make backup-verify BUNDLE=<directory>"; exit 2; }
	cd $(API) && uv run python -m abb_api.backup verify "$(BUNDLE)" $(BACKUP_FLAGS)

restore:
	@test -n "$(BUNDLE)" || { echo "usage: make restore BUNDLE=<directory> ARTIFACT_DIR=<empty directory>"; exit 2; }
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && uv run python -m abb_api.backup restore "$(BUNDLE)" $(if $(ARTIFACT_DIR),--artifact-dir "$(ARTIFACT_DIR)") $(BACKUP_FLAGS)

# Rotate the alert-encryption key and re-seal every destination; ROTATE_FLAGS="--new-key", "--dry-run" or empty to resume.
rotate-alert-keys:
	set -a && . ./$(ENV_FILE) && set +a && cd $(API) && uv run python -m abb_api.rotate alerts $(ROTATE_FLAGS)

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

# Record the scripted example agents (coding, custom, multi-agent; no API key) into the running stack (`make dev` or `make up`).
# Own project `abb-demo`; `make demo-reset` is a dry run, `make demo-reset CONFIRM=1` permanently removes only that project.
demo:
	scripts/demo.sh

demo-reset:
	scripts/demo.sh reset $(if $(CONFIRM),--confirm)

# The demo twice on a throwaway database; the normalised summaries must be identical (docs/TESTING.md "Demo determinism").
demo-e2e:
	scripts/demo-e2e.sh

# The landing page (Phase 20): `make site` builds the static export into site/out (deployed under /agent-black-box/);
# `make site-check` builds it and runs every check (scripts/check-site.sh --help lists them; --only NAME runs one).
site:
	pnpm --filter @abb/site build

site-check:
	scripts/check-site.sh

# Regenerate the committed JSON Schema and TypeScript types from the Pydantic models.
schema:
	cd $(SCHEMA) && uv run python -m abb_event_schema.export
	pnpm --filter @abb/event-schema generate
	mkdir -p $(WEB_GENERATED)
	cp $(SCHEMA)/schemas/1.0/event-types.json $(WEB_GENERATED)/event-types.json

# Fails if the committed schema/types differ from what the models generate.
schema-check:
	cd $(SCHEMA) && uv run python -m abb_event_schema.export --check
	pnpm --filter @abb/event-schema generate
	git diff --exit-code -- $(SCHEMA)/ts $(SCHEMA)/schemas
	cmp $(SCHEMA)/schemas/1.0/event-types.json $(WEB_GENERATED)/event-types.json

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
# tracked in docs/KNOWN_ISSUES.md. Python: every uv.lock (scripts/audit-lockfiles.sh, allow-list in
# scripts/audit-allowlist.txt). New advisories can fail an unchanged tree.
audit: audit-pnpm audit-lockfiles

audit-pnpm:
	pnpm audit --prod

audit-lockfiles:
	scripts/audit-lockfiles.sh

# Every GitHub Action pinned by commit sha, every container image by digest (KI-024, spec §127).
check-pins:
	scripts/check-pins.sh

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

# Sign-in, workspaces, roles and the stream re-check in a real browser against the dev-only fake OIDC provider (Phase 15).
auth-e2e:
	pnpm --filter @abb/web build && scripts/auth-e2e.sh

auth-https-e2e:
	pnpm --filter @abb/web build && scripts/auth-https-e2e.sh

# Multi-agent example (threads, asyncio, subprocess with a skewed clock) through the real API and web, as owner and viewer.
multiagent-e2e:
	pnpm --filter @abb/web build && scripts/multiagent-e2e.sh

# Reliability findings: pathological runs through ingestion, the worker and a real browser (Phase 10).
findings-e2e:
	pnpm --filter @abb/web build && scripts/findings-e2e.sh

# Security observability (Phase 13): a planted run through the real SDK, an SDK that skips redaction, capture modes, rebuild and
# re-analysis, an ingestion benchmark, and a real browser as owner, viewer and billing.
security-e2e:
	pnpm --filter @abb/web build && scripts/security-e2e.sh

# Policy engine and approvals (Phase 14): the demo paused for a human in a real browser, a replayed approval, failure modes against a live and a dead
# API, the state machine under concurrency, authorization by role, SDK safety, a dry run against an independent evaluation, and latency.
policy-e2e:
	pnpm --filter @abb/web build && scripts/policy-e2e.sh

# Alerting (Phase 17): every trigger fired exactly once to a signed webhook, a Slack-shaped receiver and an SMTP sink through the real API and worker,
# bounded retries / dead letter / auto-pause, outbound safety, content-free and secret-free, authorization, noise control, ingestion cost, and a browser.
alerts-e2e:
	pnpm --filter @abb/web build && scripts/alerts-e2e.sh

# Abuse resistance (Phase 19a): two API processes over one database, a worker, the fake OIDC provider, the web server and flood generators.
# Shared limits across processes (with a control), a failed-auth flood, quotas and their races, the edge (headers, docs, body cap, role check),
# sessions and invitations, alert ports and search fairness, the cost of it all, and a real browser.
hardening-e2e:
	pnpm --filter @abb/web build && scripts/hardening-e2e.sh

# Operability (Phase 19c): a real stack with a metrics token, every catalogue family moved by exactly the traffic driven, readiness and the
# database breaker against deliberately broken processes, the logs of every process scanned at DEBUG, the shipped Prometheus rules and Grafana
# dashboard run by the pinned images (an alert really fires), tracing into a local OTLP receiver, and the cost of metrics and tracing.
# Needs Postgres on 5433, Docker and uv; pulls only the two images pinned in docker-compose.yml (and refuses when the VM has under 3 GB free).
obs-e2e:
	scripts/obs-e2e.sh

# Data retention (Phase 19b): a real stack with the retention job as its own database login. Dry run deletes nothing, enforcement removes expired
# data in order, a crash (SIGKILL) mid-run is finished by a rerun, the kill switch and bounds, privileges, purge commands, a real browser, and the
# cost of enforcement (informational). Deterministic assertions only (docs/benchmarks/phase-19b-retention-e2e.md).
retention-e2e:
	pnpm --filter @abb/web build && scripts/retention-e2e.sh

# The performed restore drill (Phase 19b): seed, back up (module CLI, encrypted, compose service), destroy, restore, serve, upgrade an older bundle,
# rotate the alert key. Needs pg_dump, pg_restore and openssl on the PATH, and Docker for the compose step (docs/benchmarks/phase-19b-restore-drill.md).
backup-restore-e2e:
	scripts/backup-restore-e2e.sh

# Private hosting (Phase 23, ADR-178): the production overlay behind a real Caddy with its local CA, a fake https issuer, every assertion of
# scripts/hosting-verify.sh, the config and port checks and the memory peaks. Throwaway projects on ports 18080/18443; needs Docker with Compose 2.24+.
hosting-e2e:
	scripts/hosting-e2e.sh

# The same assertions against any URL: make hosting-verify URL=https://abb.example.org [VERIFY_FLAGS="--metrics-token-file F --strict"]
hosting-verify:
	@test -n "$(URL)" || { echo "usage: make hosting-verify URL=https://abb.example.org"; exit 2; }
	scripts/hosting-verify.sh "$(URL)" $(VERIFY_FLAGS)

# Evaluations, comparison and experiments: the coding agent twice (baseline, wrong-fix), owner and viewer in a real browser (Phase 11).
eval-e2e:
	pnpm --filter @abb/web build && scripts/eval-e2e.sh

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

# Local dashboards (docs/OPERATIONS.md "Local dashboards"): Prometheus on 127.0.0.1:9090, Grafana on 127.0.0.1:3001.
# The token file is created once (mode 0600, git-ignored) and nothing is printed; it is passed to the API and worker
# as ABB_METRICS_TOKEN and mounted read-only into Prometheus.
OBS_TOKEN := .local/metrics-token
observability-token:
	@mkdir -p .local && chmod 700 .local
	@test -s $(OBS_TOKEN) || (umask 077 && openssl rand -hex 24 > $(OBS_TOKEN))
	@chmod 600 $(OBS_TOKEN)

observability-up: observability-token
	@ABB_METRICS_TOKEN="$$(cat $(OBS_TOKEN))" ABB_WORKER_METRICS_PORT=9101 ABB_WORKER_METRICS_BIND=0.0.0.0 \
	  ABB_OBS_UID="$$(id -u)" ABB_OBS_GID="$$(id -g)" \
	  docker compose --profile app --profile observability up -d --build
	@echo "Grafana http://127.0.0.1:3001 (user admin), Prometheus http://127.0.0.1:9090"

observability-down:
	docker compose --profile observability rm -sf prometheus grafana
