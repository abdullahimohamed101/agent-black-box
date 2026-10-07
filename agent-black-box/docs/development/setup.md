# Setup

## Required tooling
| Tool | Version | Purpose | Present now? |
| --- | --- | --- | --- |
| Python | 3.12 | API, SDK, tests | yes (Homebrew `/opt/homebrew/bin/python3.12`); system python3 is 3.9 - do not use |
| Node.js | LTS (22+) | web app | **no** |
| pnpm | 9+ | JS workspace | **no** |
| PostgreSQL | 16+ | system of record | **no** (or via Docker) |
| Docker + Compose | recent | reproducible stack | **no** |
| Git | any | VCS | yes |
| GitHub CLI (`gh`) | any | PRs (only with approval) | yes |

## Python environment
```bash
/opt/homebrew/bin/python3.12 -m venv .venv && source .venv/bin/activate
```
Dependency locking tool (uv vs pip-tools) is decided in the Phase 0 plan.

## Install notes (require user approval)
```bash
brew install node pnpm postgresql@16     # or: brew install --cask docker / brew install colima docker
```
After installing, update `docs/KNOWN_ISSUES.md` and re-verify the affected criteria.
