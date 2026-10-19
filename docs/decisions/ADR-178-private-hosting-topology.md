# ADR-178: Private hosting topology (one VM, shared Caddy edge, compose overlay)

Status: Accepted (Phase 23, private hosting). The owner confirmed D1-D8 and accepted the plan's recommendations on D9-D14 and U1-U4.
Date: 2026-10-19
Phase: 23

## Context

Everything shipped so far runs on a developer machine or in CI. The owner wants one small server, a domain and invitation-only
Google sign-in, with a second project (ServerFlow) on the same machine. `docs/deployment.md` describes an override file and a
reverse proxy checklist, but states that neither was ever run end to end (KI-188). Nothing here may change the event contract,
the schema or the SDK.

## Decision

1. **One VM, Docker Compose, one project per app (D2).** Each app has its own compose project and its own PostgreSQL; a database
   is never shared. A shared Caddy project (`/opt/edge`) is the only thing that listens on 80/443 and obtains certificates.
2. **One hostname for the app (D9).** `abb.<domain>` serves the dashboard and, by path, the ingestion API: `/v1/*` and `/healthz`
   go to `api:8000`, everything else to `web:3000`. `/readyz`, `/metrics`, `/docs` and `/openapi.json` answer 404 publicly.
   One origin keeps `__Host-` cookies, CORS and the SDK base URL simple. A separate `api.<domain>` stays possible by editing the
   Caddyfile.
3. **Network topology (D10).** A Docker network `edge`, created once outside any project. Caddy and each app's `web` and `api`
   join it; the apps keep their own default network, so databases are unreachable from `edge`. No service publishes a port to
   the host (`ports: !reset []`), because Docker publishes around the host firewall: "no published ports" is the control, the
   firewall is the second layer.
4. **Metrics and admin UIs are never public (D11).** `/metrics` is not routed. Prometheus scrapes `api:8000` over the private
   compose network (this also resolves the plain-HTTP concern of KI-222 by topology). Prometheus and Grafana bind `127.0.0.1`
   and are reached through an SSH tunnel.
5. **A compose overlay, not a fork (D12).** `deploy/hosting/compose.production.yml` is merged over `docker-compose.yml` with `-f`.
   Secrets come from a 0600 `.env` outside git. The overlay sets `ABB_ENVIRONMENT=production`, `ABB_TRUST_PROXY=1` (api, web),
   the public origins, the OIDC settings and per-container memory limits, and moves `fake-oidc` to a profile that is never
   started. It needs Compose 2.24 or newer (`!reset` and `!override`); the bootstrap script checks.
6. **Proxy responsibilities.** The applications keep owning security headers, nonce CSP and HSTS (ADR-155); Caddy neither adds
   nor strips them, so the two can never disagree. Caddy owns TLS, the http-to-https redirect, the 8 MiB body limit, header and
   body timeouts, compression (not on `text/event-stream`), response buffering off for streams, and replaces any client-supplied
   `X-Forwarded-For` so the last entry is the client address.
7. **Host tooling is not a project dependency (D13).** `rclone`, `fail2ban`, `ufw` and `unattended-upgrades` are installed on
   the server by a bootstrap script and never enter a lockfile.
8. **Code changes only on proof (D14).** The preferred outcome is zero changes under `apps/` and `packages/`. A change is made
   only when the local rig proves a gap, as its own commit with a failing test first and an amendment to this ADR.
9. **The verification boundary.** The proxy chain is proven locally with the real overlay behind real Caddy using its local CA
   (`tls internal`) on a `*.localhost` name, and `scripts/hosting-verify.sh <base-url>` runs the same assertions against any
   URL. A local CA does not exercise ACME, DNS, port 80/443 reachability, Google, Backblaze, Hetzner firewalls or `sshd`. Those
   stay `UNVERIFIED (env)` until the owner-gated live run (G1, G2), and no document may call them verified before then.

## Alternatives considered

- Vercel or another serverless host: the product needs an always-on API, worker and retention job, SSE and a persistent disk.
- Coolify or another PaaS: one more moving part to secure and upgrade; may be mentioned as optional.
- Kubernetes, multiple machines, managed Postgres: out of scope for a one-person private instance.
- Caddy per app, each on 80/443: impossible on one machine, and a second ACME client on the same ports.
- Separate `api.<domain>`: a second certificate, CORS and cookie scope questions for no gain at this size.
- Terminating TLS inside the web container: `next start` has no https mode; the existing `scripts/tls-proxy.mjs` is a test tool.

## Consequences

- A new `deploy/hosting/` tree, scripts `scripts/hosting-*.sh`, and a hosting guide and runbook are added; the apps are not
  touched unless the rig proves a gap (then this ADR is amended).
- KI-188 can close once the rig passes; KI-220 is addressed by topology (config only); KI-222 by topology.
- The shared Caddy is a single point of failure for both apps; `caddy validate` before reload and one file per site limit the risk.
- Memory limits are ceilings measured in the rig, not reservations; the guide states the arithmetic next to ServerFlow's.

## Amendment 1 (2026-10-19, step 11): the hosted PostgreSQL is pinned to 17

**Decision.** `deploy/hosting/compose.production.yml` overrides the development file's PostgreSQL 16 with `postgres:17-alpine`, pinned by digest, for the `postgres` service.

**Reason.** The API image ships a PostgreSQL 17.11 client. Its `pg_restore` sends `SET transaction_timeout`, which a version 16 server rejects, so a bundle taken inside the image cannot be restored into a 16 database. This was found by the first restore drill in `scripts/hosting-backup-e2e.sh` (Phase 19b's drill had used native tools that matched the server and ran only `create` and `verify` in the container). A hosted instance is a fresh install with no 16 data to carry over, so 17 costs nothing there, and the restore drill (D6, a hosted acceptance criterion) needs it.

**What stays the same.** Development, CI and `docker-compose.yml` stay on PostgreSQL 16: no change under `apps/`, no migration, no schema or event-contract change. The rig evidence of steps 2-5 was collected on 16; the final `scripts/hosting-e2e.sh` ran on 17 (`HOSTING-VERIFY: 30 passed, 0 failed, 0 skipped`, memory peaks about 516 MiB in total).

**Consequences.** (1) The hosted database and the development database differ by a major version. The code uses nothing version-specific that the tests on 16 and the rigs on 17 have shown to differ, but the suites run on 16 only, so a 17-only behaviour difference would first appear in a hosted instance (the rigs are the guard). (2) The development stack still cannot restore a bundle through the `backup` service (KI-310). (3) A major version is never changed in place: `scripts/hosting-upgrade.sh` refuses a release that changes the PostgreSQL image; a later major needs a dump and restore.

**Reversal.** Remove the `image` override in the overlay to fall back to 16, at the price of the container restore (use a 16 client in the image, or restore with native tools matching the server). A hosted volume created on 17 cannot be opened by 16: going back means a dump from the 17 server restored into a new 16 volume. The cleaner resolution is KI-310 (a 16 client in the image or 17 in the development file), after which the override can go.
