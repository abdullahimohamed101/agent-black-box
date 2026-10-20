# Private hosting recipe (Phase 23, ADR-178)

Files for running Agent BlackBox on one small server behind a shared Caddy that obtains HTTPS certificates. This directory is a
recipe, not a certified product: the local rig (`scripts/hosting-e2e.sh`) proves the proxy chain against Caddy's local CA, and
live issuance, real Google sign-in and real server hardening stay `UNVERIFIED (env)` until the owner runs them.

Plan: `docs/plans/completed/phase-23-private-hosting.md`. Decision: `docs/decisions/ADR-178-private-hosting-topology.md`.

## What is here

| Path | What it is | Verified by |
| --- | --- | --- |
| `compose.production.yml` | production overlay: no published ports, `ABB_ENVIRONMENT=production`, PostgreSQL 17, memory ceilings | `scripts/hosting-verify.sh --config-only`, `scripts/hosting-e2e.sh` |
| `edge/` | the shared Caddy (automatic HTTPS) and the Agent BlackBox site; ServerFlow example | `scripts/hosting-e2e.sh` |
| `compose.observability.yml`, `prometheus/`, `alertmanager/`, `blackbox/`, `rules/` | monitoring: Prometheus 30 d, node_exporter, Alertmanager, blackbox_exporter, Grafana; host, backup and certificate alerts with promtool unit tests | `scripts/hosting-obs-e2e.sh` |
| `systemd/` | `abb-backup` (nightly encrypted backup + off-site copy) and `abb-container-metrics` units, `backup.env.example` | `scripts/hosting-backup-e2e.sh` |
| `rig/` | local test overlays; never used on a server | |

Scripts (all in `scripts/`): `hosting-bootstrap.sh` (fresh Ubuntu 24.04 server), `hosting-secrets.sh`, `hosting-upgrade.sh`, `hosting-backup.sh`, `hosting-restore-drill.sh`, `hosting-container-metrics.sh`, `hosting-verify.sh` (`--server`, `--server-probe`). Each has a rig named after it (`make hosting-bootstrap-e2e`, `hosting-upgrade-e2e`, `hosting-backup-e2e`, `hosting-obs-e2e`, `hosting-e2e`).
