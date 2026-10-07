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
