# Setup

## Required tooling
| Tool | Version | Purpose | Present now? |
| --- | --- | --- | --- |
| Python | 3.12 | API, SDK, tests | yes (managed by `uv`) |
| uv | 0.12+ | Python env + lockfile | yes |
| Node.js | 22+ | web app | yes |
| pnpm | 12 | JS workspace | yes |
| PostgreSQL | 16 | system of record | yes, via Compose (host port 5433); `postgresql@16` is also installed natively but unused |
| Docker + Compose | recent | reproducible stack | yes (Colima VM: `colima start`) |
| Git | any | VCS | yes |
| GitHub CLI (`gh`) | any | PRs (only with approval) | yes |

## First-time setup
```bash
cp .env.example .env && make setup
```
`make setup` runs `uv sync` in `apps/api`, `pnpm install --frozen-lockfile`, starts Postgres
(`docker compose up -d --wait postgres`) and applies migrations.

## macOS notes
- Docker runs through Colima: `colima start --cpu 2 --memory 4` once per boot (installed 2026-10-07).
  Compose needs `"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` in `~/.docker/config.json`.
- Dev Postgres is published on 5433 so it never clashes with a native Postgres on 5432.
- pnpm 12 fails installs on unreviewed dependency build scripts; decisions are in `pnpm-workspace.yaml` (`allowBuilds`).
- Version pins that matter: TypeScript 6.x and ESLint 9.x (typescript-eslint / eslint-plugin-react do not yet support TS 7 / ESLint 10).

## Running the pieces

| Goal | Command |
| --- | --- |
| API + web natively (Postgres in Compose) | `make dev` |
| Background worker natively | `cd apps/api && uv run python -m abb_api.worker` (needs `.env` exported) |
| Everything in containers | `make up`, then `make smoke` |
| Provision a tenant and key | `make seed` (local dev key) or `python -m abb_api.cli --help` |
| Latency baseline | `make seed && make bench` (read `docs/benchmarks/phase-2-ingestion.md` first) |

Native `make dev` does not start the worker, so runs stay `processing` unless you start one (or use `make up`).

## Web app against the API (Phase 4)
`pnpm --filter @abb/web dev` serves the UI on 3000 (`next dev --port 3100` for a second instance). It reads the API through a
server-side proxy (ADR-021): set `ABB_WEB_API_KEY` (a workspace-wide `runs:read` key) and `ABB_API_INTERNAL_URL` in `.env`, or set
`ABB_WEB_DATA_SOURCE=fixtures` to browse deterministic fixtures with no API. Open `/w/<workspace>/projects/all`.
