# Phase 23: Private hosting (one small server, public HTTPS)

**Status:** In progress (2026-10-19): steps 1-6 (local verification) being built; steps 7-12 and G1/G2 not started. Originally blocked on PR #300 (Phase 20) merging, because this plan edits `docs/deployment.md`, the README, the release checklist and the docs checkers that Phase 20 created.
**Owner:** the lead agent builds steps 1-12; the project owner performs the gated steps G1 and G2 (accounts, money, DNS, credentials).
**Depends on:** Phase 19 (hardening, merged), Phase 20 (`feature/phase-20-release-polish`, PR #300 open). Branch `feature/phase-23-hosting`, stacked on the Phase 20 tip `2e91913`; rebase onto `main` once #300 merges.
**Numbering:** Phases 21 and 22 are already used (UI shell, run-page redesign; both merged), so this is Phase 23. Phase 18 stays deferred.
**Spec refs:** §62 (invariants), §94 (retention), §116-§117 (metrics, SLOs), §125 (runbooks); ADR-060, 152, 153, 155, 156, 159-162, 165, 168, 169, 171, 173, 175.

## Outcome

A person who has a Hetzner server, a domain and a Google OAuth client can follow `docs/hosting-guide.md` and end with Agent BlackBox reachable at `https://abb.<domain>` over automatic HTTPS, invitation-only Google sign-in, nightly encrypted off-site backups with a proven restore, and monitoring that includes disk and memory. The proxy chain (headers, `ABB_TRUST_PROXY`, HSTS, `__Host-` cookies, CSP, SSE, flood limits, `/metrics` exposure) is proven locally and re-checkable against any URL by `scripts/hosting-verify.sh <base-url>`. The same server can host a second project (ServerFlow) without sharing a database.

The honest claim after this phase: the recipe and every local assertion are VERIFIED; live Let's Encrypt issuance, real Google sign-in, the real B2 upload and the real server hardening are `UNVERIFIED (env)` until the gated steps run.

## Non-Goals

- Not Vercel or any serverless host (D4). A split deploy (web on a static/edge host, API elsewhere) is described as a possible later variant only, not built.
- Not Coolify or another PaaS (optional, mentioned only).
- Not Kubernetes, multi-machine, HA, managed Postgres, WAL archiving or point-in-time recovery (RPO is the backup interval), object storage for artifacts (Phase 18, deferred), autoscaling.
- No other OIDC providers built (KI-069 stays: documented only). No SDK, schema, migration or event-contract change.
- No modification of the ServerFlow repository. Its route is a documented example in this repository.
- Not a multi-tenant SaaS: access is invitation-only (ADR-157) and the owner invites people.

## Current Architecture (what exists today)

- `docker-compose.yml`: `postgres`, one-shot `migrate` (owner role), `api` (:8000), `worker`, `retention`, `web` (:3000), dev-only `fake-oidc` (shares the API's network namespace), `backup` (profile `ops`), `prometheus` and `grafana` (profile `observability`, tmpfs data, 24 h). Every published port is `127.0.0.1`. Images are digest-pinned (`scripts/check-pins.sh`, Dependabot `docker-compose` ecosystem for `/`).
- Everything sets `ABB_ENVIRONMENT: development` (fixed in the file); the compose file passes no `ABB_TRUST_PROXY`, no `ABB_OIDC_EXTRA_HOSTS`, no `ABB_CORS_ORIGINS` override point other than editing; `docs/deployment.md` documents an override file for those (untested, stated as such).
- The browser talks only to the web origin: the web server's read proxy (`apps/web/src/app/api/abb/[...path]/route.ts`, ADR-021) forwards the session cookie to the API at `ABB_API_INTERNAL_URL`; it forwards an allowlist of headers and drops `X-Forwarded-*` from the browser (`apps/web/src/server/upstream.ts`). SDKs and API keys talk to the API directly (`/v1/*`). The sign-in redirect URI is `<ABB_WEB_ORIGIN>/api/auth/callback`.
- Edge behaviour owned by the apps (ADR-155): nonce CSP on the web, deny-all CSP on the API, HSTS when `ABB_WEB_ORIGIN` is https, body cap, `/docs` off in production. `ABB_TRUST_PROXY=1` makes the API (failed-auth and failed-session throttles) and the web server (sign-in start limiter, `loginLimiter.ts`) use the last `X-Forwarded-For` entry.
- Metrics: `/metrics` exists only with `ABB_METRICS_TOKEN`; the bucket is global and checked before the token (KI-220); plain HTTP (KI-222). Prometheus/Grafana/rules in `deploy/observability/`.
- Backups: `python -m abb_api.backup create|verify|restore` (owner role, encrypted bundles, textfile metrics), runbook `docs/runbooks/backup-restore.md`, performed drill in `scripts/backup-restore-e2e.sh`.
- Test-only TLS: `scripts/tls-proxy.mjs` + `scripts/auth-https-e2e.sh` (self-signed, in front of `next start`).
- Measured by the owner (D3): ServerFlow's 17 containers idle 721 MiB (733 MiB under smoke, enforced limits sum about 2.4 GiB); ABB dev stack about 470 MiB; both under 1.3 GiB idle. ServerFlow publishes gateway `127.0.0.1:18080`, control plane `:19090`, Grafana `:13000`, Prometheus `:19091`, Jaeger `:16686`.

## Known issues considered

| KI | Disposition |
| --- | --- |
| KI-188 (proxy chain untested) | Pulled in. Closed by steps 4-6 (real Caddy terminator, `hosting-e2e`). Criterion A5. |
| KI-220 (`/metrics` on the public listener, shared bucket) | Pulled in, config only: Caddy answers 404 for `/metrics` on the public site; Prometheus scrapes `api:8000` over the private compose network; no port published. Code change (separate private port) stays deferred unless the rig shows the bucket reachable. Criterion A6. |
| KI-222 (metrics over plain HTTP) | Pulled in by topology: the scrape never leaves the Docker network. Documented; worker TLS option stays deferred. |
| KI-073 (trust proxy, sign-in start limits) | Pulled in as verification, not code: with `ABB_TRUST_PROXY=1` on `api` and `web` and Caddy as the only path, assert per-address limiting. Open question U2 (below): the web read proxy drops browser `X-Forwarded-*`, so API-side per-address throttling only sees the real client on direct `/v1/*` traffic; confirm in the rig whether that is acceptable. A code change is allowed only if the rig proves a real gap, as its own step (6) with an ADR. |
| KI-069 (`email_verified` omitted by some providers) | Stays deferred: Google sends it. The guide says other providers are documented in the auth runbook, not built. |
| KI-243 (unscheduled backup is invisible) | Pulled in: step 9 adds an `absent()`-style first-run check to `hosting-verify.sh --server` and a Prometheus rule for the hosting profile. |
| KI-244 (local Prometheus is 24 h tmpfs) | Pulled in for hosting: the hosting observability overlay uses a persistent volume and 30 d retention. |
| KI-245 (runbook_url is a repo path) | Pulled in: Alertmanager template in step 9 rewrites the prefix to a configurable docs URL. |
| Open S1 items | None open (S1 list is empty at this date; KI-019 and KI-018 resolved in 19a). Re-check at step 1. |

## Decisions

Confirmed by the owner (D1-D8) unless marked otherwise. D9-D14 are proposed by this plan; the first three become ADR-178.

- **D1 Audience.** The public internet, with a domain and HTTPS. Not private-network-only. Consequence: abuse rails and invitation-only access are the exposure control; the host is hardened and only 80/443 (and 22) are reachable.
- **D2 Platform.** One small Hetzner Cloud VM running Docker Compose. A shared Caddy is the single front door (automatic HTTPS). One compose project per app, each with its own Postgres (never share a database). Agent BlackBox is tenant one, ServerFlow tenant two. Layout in "Proposed Design".
- **D3 Size.** Start on 2 vCPU / 4 GB (CX23, about EUR 4-4.5/month); documented resize-up path to 4 vCPU / 8 GB (CX33, about EUR 6.5-8.5) if memory pressure shows. Prices change: always confirm on Hetzner's page. Region is **[NEEDS INPUT]** (EU Germany/Finland is cheapest; US locations exist). Resize on Hetzner keeps the disk if "CPU and RAM only" is chosen (reversible); a disk upgrade is not reversible: the guide says so.
- **D4 Not Vercel, not Coolify.** The product needs an always-on API, worker and retention job, SSE streaming, Postgres and a persistent disk. Split-deploy is a documented possible later variant. Coolify/PaaS adds a moving part; optional mention.
- **D5 Sign-in.** Google OIDC, open to anyone the owner invites (invitation-only already). The owner creates the OAuth client in Google's console (exact steps and redirect URIs in the guide, step 10). `ABB_OIDC_EXTRA_HOSTS=oauth2.googleapis.com,www.googleapis.com` is required and must be passed by the production overlay.
- **D6 Backups.** Nightly encrypted bundles via the existing backup tool, copied to a second provider (Backblaze B2 recommended; Hetzner Storage Box the alternative; **[NEEDS INPUT]** which), plus Hetzner automatic server backups (or snapshots). A restore drill into a throwaway database is an acceptance criterion.
- **D7 Verification without a domain.** Prove the chain locally under Colima/Docker with Caddy's local CA (`tls internal`) and `*.localhost` names, then ship `scripts/hosting-verify.sh <base-url>` that runs identical assertions against any URL. Live Let's Encrypt and real Google sign-in are `UNVERIFIED (env)` until G1/G2.
- **D8 Deployment.** Recipe and docs are built by the lead; the real deployment is gated (G1, G2). The owner creates and pays for server, domain, DNS, Google client and B2 account. Credentials are never typed by an agent: the owner uses their own shell and password manager; the lead may drive SSH only with the owner present, or hand over step-by-step commands.
- **D9 (proposed) One hostname for the app.** `abb.<domain>` serves the dashboard and, by path, the ingestion API: `/v1/*` and `/healthz` to `api:8000`, everything else to `web:3000`. One origin keeps `__Host-` cookies, CORS and the SDK base URL simple (`https://abb.<domain>`). `/readyz`, `/metrics`, `/docs` and `/openapi.json` return 404 publicly (readiness is probed from inside the network). Rejected alternative: a separate `api.<domain>` (second cert, CORS, cookie scope questions) — still possible by changing the Caddyfile.
- **D10 (proposed) Network topology.** A Docker network `edge` created once; Caddy joins it; each app's `web` and `api` also join `edge`, with all `ports:` removed (`!reset`). Apps keep their own default network, so databases are unreachable from `edge`. Docker bypasses ufw for published ports, so "no published ports" is the control, not the firewall alone.
- **D11 (proposed) Metrics and admin UIs are never public.** Prometheus scrapes inside the project network; Grafana and Prometheus bind `127.0.0.1` and are reached by SSH tunnel. Optional later: Caddy `basic_auth` vhost (documented, off by default). `/metrics` is not routed by Caddy.
- **D12 (proposed) Compose overlay, not a fork.** `deploy/hosting/compose.production.yml` is merged over `docker-compose.yml` with `-f`; secrets come from a 0600 `.env` owned by the `deploy` user, outside git. `fake-oidc` is removed via `profiles: !override ["dev-only"]`, `ABB_ENVIRONMENT=production` on migrate/api/worker/retention/backup. Requires Compose 2.24+ (state it; check in bootstrap).
- **D13 (proposed) Host tooling is not a project dependency.** `rclone` (B2 upload), `fail2ban`, `ufw`, `unattended-upgrades` are installed on the server by the bootstrap script, never added to the repository's lockfiles. The five dependency questions are answered in the guide.
- **D14 (proposed) Code changes only on proof.** The preferred outcome is zero changes under `apps/` and `packages/`. Step 6 is a conditional branch point.
- **ADR:** `docs/decisions/ADR-178-private-hosting-topology.md` (D2, D9-D12, and the `UNVERIFIED (env)` boundary), written in step 1. Next free number after ADR-177.

## Proposed Design

### Server layout

```text
/opt/edge/                      shared Caddy project (compose.yml, Caddyfile, sites/*.caddy, data volume)
/opt/abb/                       checkout of agent-black-box; .env (0600, deploy:deploy); backups/ (0700)
/opt/serverflow/                (example only) its own checkout and compose project, its own Postgres
docker network "edge"           Caddy + each app's web/api (or gateway) only
```

Caddy: `/opt/edge/Caddyfile` has `import sites/*.caddy`; `sites/abb.caddy` is generated from `deploy/hosting/edge/abb.caddy.template`. Requirements for the ABB site:

- automatic HTTPS (ACME email from `.env`); `tls internal` only in the local rig (templated switch);
- `reverse_proxy` to `api:8000` for `/v1/*` and `/healthz`, to `web:3000` otherwise; `flush_interval -1` and no response buffering on SSE (`/v1/runs/*/stream` and web `/api/abb/*` stream paths), read/write timeouts above 15 s for event streams;
- `request_body { max_size 8MB }` (matches the API's 8 MiB ceiling); header/body timeouts 30-60 s;
- `encode zstd gzip` but not on `text/event-stream`;
- headers: do not set, strip or override app security headers (nonce CSP). Caddy adds only `-Server` and `Via`-free defaults. HSTS comes from the app (ADR-155), not Caddy, so the two cannot disagree; the verify script asserts it is present exactly once;
- `X-Forwarded-For`: Caddy replaces any client-supplied value with the client address (its default when no `trusted_proxies`), so the last entry is the client; asserted by sending a forged header;
- access log: JSON, fields method, host, path (no query string), status, duration, size, remote IP; no headers, no bodies; rotated (`roll_size`, `roll_keep`);
- http to https redirect (Caddy default); HTTP/3 optional (UDP 443 opened in ufw if kept).

ServerFlow route example (documentation + `deploy/hosting/edge/serverflow.caddy.example`, never applied by us): `sf.<domain> { reverse_proxy serverflow-gateway:8080 }` with the gateway container attached to `edge` through an owner-side override file kept in the ServerFlow checkout or in `/opt/serverflow/compose.edge.yml` (not committed to the ServerFlow repository), its other loopback ports left unpublished or tunnelled, and its own `mem_limit` values unchanged. The control plane (`:19090`) and Grafana (`:13000`) stay behind SSH tunnels.

### Production overlay (`deploy/hosting/compose.production.yml`)

- `ports: !reset []` for `postgres`, `api`, `web`, `prometheus`, `grafana` except the two observability UIs rebound to loopback; `fake-oidc` moved to a never-started profile;
- environment (values from `.env`, none committed): `ABB_ENVIRONMENT=production`; `ABB_WEB_ORIGIN=https://abb.<domain>` (api, web); `ABB_CORS_ORIGINS=https://abb.<domain>`; `ABB_TRUST_PROXY=1` on `api` and `web`; `ABB_OIDC_ISSUER=https://accounts.google.com`, `ABB_OIDC_CLIENT_ID`, `ABB_OIDC_CLIENT_SECRET`, `ABB_OIDC_EXTRA_HOSTS`; `ABB_METRICS_TOKEN` (24+ chars); `ABB_RETENTION_MODE=dry_run` initially; the three generated Postgres role passwords; `ABB_RETENTION_DATABASE_URL`; `ABB_ALLOW_DEV_SESSIONS` never;
- `NEXT_PUBLIC_API_URL=https://abb.<domain>` as the web build arg;
- `restart: unless-stopped` everywhere; `mem_limit` per container (starting values to be measured in step 5: postgres 768m, api 512m, worker 384m, retention 192m, web 384m, caddy 128m; summing under 2.4 GiB leaves headroom next to ServerFlow's 2.4 GiB limit sum only because actual use is far lower; the guide states the arithmetic and that limits are ceilings, not reservations); `logging: json-file` with `max-size: 10m`, `max-file: 3`;
- `networks: edge` attachment for `web` and `api`.

### Host hardening (`scripts/hosting-bootstrap.sh`, idempotent, run once as root)

Create `deploy` user (docker group membership noted as root-equivalent), SSH key-only login (`PasswordAuthentication no`, `PermitRootLogin no`, keys verified before reload), ufw default deny with 22/tcp, 80/tcp, 443/tcp (and 443/udp if HTTP/3), fail2ban sshd jail, unattended-upgrades (security only; reboot window documented, Docker restarts containers on reboot via `restart: unless-stopped`), `/etc/docker/daemon.json` log rotation and `live-restore`, a 2 GB swapfile (small-VM safety net), chrony/timesyncd check, `docker compose version >= 2.24` check, `docker network create edge`, secrets directory modes. It prints what it changed and refuses to lock out SSH (leaves the current session alive; documents the second-session test).

### Secrets

`scripts/hosting-secrets.sh` generates `/opt/abb/.env` (umask 077, `deploy:deploy`, mode 0600) from `.env.production.example` (names only, no values) using `openssl rand -hex`, never printing values; refuses to overwrite; the OIDC client secret and B2 key are entered by the owner into the file in their own editor. Postgres passwords must exist before the first start (stored at volume creation). The backup passphrase lives in `/etc/abb/backup.pass` (0600) and in the owner's password manager (losing it makes bundles unreadable; the guide says so).

### Migrations and first deploy ordering

`postgres` (healthy) then `migrate` as owner role (`alembic upgrade head`, applies runtime and retention role passwords) then `api`, `worker`, `retention` (retention in dry_run), then `web`, then Caddy site enabled last (so no traffic arrives before `/healthz` is green). First owner is created with the CLI per the auth runbook (nobody is seeded in production). Upgrades: backup, verify, `git fetch`, checkout a tag, build, `up -d --build --wait`; rollback = previous tag plus the migration safety table (`docs/runbooks/migrations.md`): migrations that cannot be rolled back mean "restore from the pre-upgrade backup", stated in the runbook. `scripts/hosting-upgrade.sh` does backup, verify, deploy, readiness wait, `hosting-verify.sh` and auto-reports (does not auto-roll-back a migrated database).

### Dependabot-bumped images

Compose and Dockerfile digests are already bumped by Dependabot PRs and checked by `scripts/check-pins.sh` (which scans `compose*.yml` files; `deploy/hosting/` files follow that name pattern, and the Caddy image is added with a digest). Rollout rule: merge to `main`, then on the server `hosting-upgrade.sh <tag-or-main>`; a Postgres major is never automatic (dump/restore procedure referenced). A minimal "what changed" check: `docker compose pull --dry-run` plus `git log` between tags.

### Monitoring and disk

`deploy/hosting/observability.compose.yml` (overlay): Prometheus with a persistent volume and 30 d retention, Grafana (loopback), node_exporter (host metrics + textfile collector for backup metrics), cAdvisor optional (memory per container; skipped by default to save RAM), Alertmanager with a configurable receiver (**[NEEDS INPUT]** email vs Slack/webhook) and a runbook-URL rewrite template (KI-245). New rules `deploy/hosting/rules/host.yml`: root disk and Docker data disk above 80 percent (warn) and 90 percent (page), inode exhaustion, predicted fill within 24 h (`predict_linear`), memory available below 10 percent for 10 min, swap in use, container restarts, certificate expiry (Caddy metrics endpoint on the private network) under 14 days, backup age and the KI-243 absent check. The owner's laptop Docker disk filled once: `docker system df` in the weekly check, `docker image prune` after upgrades (never `volume prune`; stated loudly), log rotation prevents the common cause. External uptime check (**[NEEDS INPUT]**: UptimeRobot / Healthchecks.io / Better Stack free tier, or none): probes `https://abb.<domain>/healthz` and a dead-man's-switch ping from the backup script. Logs: `docker logs` rotated by the daemon, Caddy access log on a rotated file, `journalctl` for the host; nothing ships off-box by default; app logs contain no payloads.

### Backups

`scripts/hosting-backup.sh` (systemd timer `abb-backup.timer`, 02:30): retention is not running at that time check; `backup create --encrypt --passphrase-file`, `backup verify`, `rclone copy` to `b2:<bucket>/abb/<date>` using an application key limited to one bucket and write/list (no delete) where the provider allows it, remote verify (size and checksum listing), local pruning at 7 days, remote lifecycle rule at 30 days (set in the provider's console), textfile metrics written for node_exporter, ping the dead-man URL. `scripts/hosting-restore-drill.sh <bundle-or-remote-path>`: downloads, verifies, restores into a throwaway database and artifact directory (`abb_restore_drill`, removed after), runs `python -m abb_api.cli` or SQL count checks against the manifest, opens `/readyz` of a second API instance on a scratch port, prints `DRILL OK` or fails, always cleans up. Hetzner server backups cover host rebuild speed, not data consistency (a live Postgres file copy is crash-consistent only): the logical bundle is the authoritative backup.

### Verification rig and `hosting-verify.sh`

`scripts/hosting-e2e.sh` (make target `hosting-e2e`): on Colima/Docker, start the real overlay with Caddy `tls internal` on `https://abb.localhost:8443` (high ports so no root), a fake OIDC provider reachable as an https issuer (`oidc.abb.localhost`, behind the same Caddy, with the Caddy root CA mounted into the API container and a network alias), production environment, a throwaway project name and volumes; then run `scripts/hosting-verify.sh https://abb.localhost:8443 --cacert <root.crt> --oidc-fake` and a Playwright sign-in spec. If the fake provider cannot satisfy the production https-issuer rule without code change, fall back to asserting everything except the browser sign-in in production mode and run sign-in in the https e2e (existing `auth-https-e2e.sh`) — decided in step 4 (spike), recorded in the plan, no weakening of production checks.

`scripts/hosting-verify.sh <base-url> [--cacert F] [--metrics-token-file F] [--oidc-fake] [--server]` assertions (each prints PASS/FAIL, exit non-zero on any FAIL, never prints tokens or cookies):

1. Certificate valid for the host and chain verifies (with `--cacert` locally; system roots live); http redirects to https.
2. HSTS present exactly once on `/` and `/v1/...`; CSP on `/` has a per-request nonce that differs across two requests and no `unsafe-inline` for scripts; `nosniff`, frame denial, `no-store` on API responses; no `Server`/`X-Powered-By` leakage beyond policy.
3. `/docs`, `/openapi.json`, `/readyz` (public), `/metrics` return 404 on the public origin; `/metrics` with and without bearer token is still 404.
4. `/healthz` 200; `/v1/auth/me` without cookie 401; sign-in start sets the `__Host-abb_login` cookie with `Secure; HttpOnly; Path=/` and no Domain attribute (`--oidc-fake` adds the full sign-in and `__Host-abb_session` check).
5. Forwarded-for handling: a request with a forged `X-Forwarded-For` does not let a client evade per-address failed-auth throttling or frame another address (send N bad keys with rotating forged headers: still throttled after the budget; a different real source is not affected where testable).
6. SSE: a stream endpoint delivers its first event within a bound without waiting for buffer fill, and a heartbeat arrives within the timeout (proves buffering off and timeouts above 15 s).
7. Body limit: 9 MiB POST refused with 413 at the proxy, a 1 MiB valid-shaped ingest request reaches the API (4xx from auth is acceptable and proves the path).
8. Compression: `Accept-Encoding: gzip` on a text asset is encoded; the event stream is not.
9. Flood limits: a burst of unauthenticated requests to a limited route yields 429 with `Retry-After` and `/healthz` stays 200 meanwhile.
10. Open ports (only with `--server`, run from the server itself or via a given host): TCP connect to 5433, 8000, 3000, 3001, 9090 on the public IP fails; only 80/443 answer. Also checks, over SSH-free local commands, `ufw status`, `docker ps` published ports, `.env` mode 0600, `sshd -T` key-only, backup timer active and a backup newer than 36 h (KI-243).

The script depends on `curl`, `openssl` and python3 stdlib only (a helper `scripts/hosting_verify_lib.py` for header and SSE parsing); it has a self-test (`scripts/tests/test_hosting_verify.py`) against a tiny local fake server that serves good and deliberately bad responses, so each assertion is shown to fail when it should (mutation-style: planted violations).

### Code changes (conditional)

None planned. Likely config-only items: pass `ABB_TRUST_PROXY`, `ABB_OIDC_EXTRA_HOSTS`, `ABB_CORS_ORIGINS` through the overlay (no change to `docker-compose.yml` beyond, at most, making the hardcoded `ABB_ENVIRONMENT`/`ABB_CORS_ORIGINS` overridable, which the overlay does anyway). Step 6 is taken only if the rig shows (a) the web-to-API hop makes per-address API throttling wrong for browser traffic in a way that matters, (b) `/metrics` reachable and bucket spendable through any public path, or (c) a header conflict. Each such change gets a fail-before test, an ADR and a security review note.

## Affected Files

New: `docs/plans/active/phase-23-private-hosting.md` (this), `docs/decisions/ADR-178-private-hosting-topology.md`, `docs/hosting-guide.md`, `docs/runbooks/hosting-operations.md` (upgrade, rollback, disk full, certificate, restore from B2, resize, add a second app), `deploy/hosting/compose.production.yml`, `deploy/hosting/observability.compose.yml`, `deploy/hosting/edge/{compose.yml,Caddyfile,abb.caddy.template,serverflow.caddy.example}`, `deploy/hosting/rules/host.yml`, `deploy/hosting/alertmanager/`, `deploy/hosting/systemd/{abb-backup.service,abb-backup.timer}`, `.env.production.example`, `scripts/hosting-{bootstrap,secrets,backup,restore-drill,upgrade,verify,e2e}.sh`, `scripts/hosting_verify_lib.py`, `scripts/tests/test_hosting_verify.py`, a Playwright spec for the rig.
Edited: `docs/deployment.md` (point to the guide, keep the single-machine scope text accurate), `docs/IMPLEMENTATION_PLAN.md`, `docs/PROJECT_STATE.md`, `docs/OPERATIONS.md` (hosting section), `docs/runbooks/README.md`, `docs/runbooks/backup-restore.md` (off-site section), `docs/KNOWN_ISSUES.md` (close KI-188, update 220/222/243/244/245, file new ones), `README.md` (link), `Makefile` (`hosting-e2e`, `hosting-verify`), `scripts/docs_commands_check.py` and `scripts/release_checklist.py` allow-lists if they need the new paths, `.github/dependabot.yml` (add `/deploy/hosting` and `/deploy/hosting/edge` docker-compose directories), `scripts/check_pins.py` only if its scan misses the new files, `.gitignore` (nothing secret).
Not touched: `apps/`, `packages/`, migrations, the ServerFlow repository (unless step 6 triggers).

## Acceptance Criteria (command-checkable)

- **A1** `docker compose -f docker-compose.yml -f deploy/hosting/compose.production.yml config` exits 0 and its output has no `ports:` entry on postgres, api, web (and none on `0.0.0.0`), no `fake-oidc` in the default profile set, and `ABB_ENVIRONMENT: production` on migrate, api, worker, retention (script `scripts/hosting-verify.sh --config-only` greps the rendered config).
- **A2** `scripts/check-pins.sh` and `python3 scripts/check_docs_links.py README.md docs site CONTRIBUTING.md` and `scripts/docs-commands-check.sh` pass with the new files.
- **A3** `python3 -m pytest scripts/tests/test_hosting_verify.py` passes and each planted violation (missing HSTS, public `/metrics`, buffered SSE, forged-XFF evasion, wide-open port, missing `Secure`) makes the matching assertion fail.
- **A4** `make hosting-e2e` ends `HOSTING-E2E PASSED` with every `hosting-verify.sh` assertion PASS against `https://abb.localhost:8443` served by real Caddy (`tls internal`) in front of the real production-mode overlay.
- **A5** (KI-188) the rig output shows: header forwarding (forged `X-Forwarded-For` replaced), `ABB_TRUST_PROXY=1` per-address throttling observed, HSTS exactly once, `__Host-` cookies, CSP nonce changing per request, SSE first byte under the bound.
- **A6** (KI-220/222) the public origin returns 404 for `/metrics` with and without the token; `docker compose ps --format json` shows no published port for api/worker/prometheus beyond loopback; the in-network scrape returns 200 with the token (`scripts/hosting-e2e.sh` step output).
- **A7** (D6) `scripts/hosting-backup.sh` run against a local rclone `local` remote produces an encrypted bundle, a remote copy and a verify; `scripts/hosting-restore-drill.sh` restores it into a throwaway database and prints `DRILL OK` with row counts equal to the manifest; the throwaway database and directory are gone afterwards (checked by `psql -l`).
- **A8** `shellcheck scripts/hosting-*.sh` clean; `promtool check rules deploy/hosting/rules/host.yml` passes; `amtool check-config` passes (or `UNVERIFIED (env)` with the missing tool named).
- **A9** `scripts/hosting-bootstrap.sh --check` (dry-run, no changes) runs in a Debian container and lists every action; applying it for real is `UNVERIFIED (env)` until G1.
- **A10** `docs/hosting-guide.md` contains the cost table with "confirm on Hetzner" wording, the Google OAuth steps with the exact redirect URI `https://abb.<domain>/api/auth/callback`, the DNS records (A and AAAA for `abb`, optional CAA), and `docs-commands-check` validates every command and variable it names.
- **A11** `scripts/quality.sh quick` passes on the branch; `scripts/quality.sh full` is run or reported `UNVERIFIED (env)` with the reason (Phase 20 had the same limitation).
- **A12 (G1, owner)** after deployment, `scripts/hosting-verify.sh https://abb.<domain> --server` is all PASS on the real URL; Let's Encrypt certificate issued (`openssl s_client` issuer line shows Let's Encrypt); the owner signs in with Google as an invited user and a non-invited Google account is refused.
- **A13 (G2, owner)** a real restore drill from B2 (`hosting-restore-drill.sh b2:...`) prints `DRILL OK`; the Hetzner firewall/ufw/open-port check passes; disk and backup-age alerts are visible in Prometheus; a forced alert reaches the chosen receiver.

## Verification Plan

Order of evidence: unit self-test of the verifier (A3), static checks (A1, A2, A8, A9), the local rig (A4-A6), the backup drill locally (A7), docs checks (A10), `quality.sh quick` then `full` (A11), then the gated live runs (A12, A13). Each result is recorded in the plan as VERIFIED, `UNVERIFIED (env)` or ASSUMED with the command and output. Environment prerequisites: Colima/Docker (present), `mkcert` not needed (Caddy's CA), `rclone`, `shellcheck`, `promtool` (obs-e2e already uses promtool via image), a Debian image for the bootstrap dry run. Real TLS issuance, Google, B2, Hetzner firewall and `sshd` behaviour cannot be exercised before G1: they stay `UNVERIFIED (env)`.

## Risks

- **False confidence from `tls internal`.** Local CA and `*.localhost` do not exercise ACME, DNS or port 80/443 reachability. Mitigation: label clearly; G2 repeats the same script live.
- **Docker publishes around ufw.** A single stray `ports:` mapping exposes Postgres. Mitigation: A1 greps the rendered config; the `--server` check probes the public IP from outside; Postgres is on a private network only.
- **Memory pressure with two stacks on 4 GB** (OOM kills Postgres). Mitigation: per-container limits, swap, memory alerts, documented resize; measure under the rig's smoke load.
- **Lockout during SSH hardening.** Bootstrap never reloads sshd until a key login is confirmed; Hetzner console recovery documented.
- **Backup passphrase or B2 key loss; backup silently not running** (KI-243). Mitigation: first-run check, dead-man ping, age alert, drill in G2.
- **Secrets on disk** (0600 `.env` readable by anyone with docker group). Documented; backups encrypted; no secrets in git or logs; `hosting-verify.sh` prints no secret values.
- **Shared Caddy is a single point of failure for both apps** and a misconfigured ServerFlow site could break ABB's TLS. Mitigation: `caddy validate` before reload, per-site files, reload (not restart).
- **Proxy-trust misconfiguration** (`ABB_TRUST_PROXY=1` while a port is reachable directly lets clients forge addresses). Mitigation: no published ports, A1, A5.
- **Web hop drops forwarded headers** (U2): browser-originated API calls appear to come from the web container, so API per-address throttles for session traffic are coarse. Decide in step 6 with evidence.
- **Retention enforcement** deletes data permanently; ships `dry_run`; enabling requires a verified drill (existing rule).
- **Cost and price drift** and Hetzner/B2 terms: only described, never asserted as fact; owner confirms.
- **Legal/compliance:** a public instance that stores agent telemetry may hold personal or confidential data; the guide notes METADATA_ONLY default, retention settings and that the owner is the operator. No compliance claims are made.

## Ordered Steps

Each step is one commit on `feature/phase-23-hosting` (`type(scope): ...`, trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`), small enough for a Sonnet builder, with its own tests where it adds logic. Local verification lands first, docs next, the gated deployment last.

1. **ADR-178 and skeleton.** Write the ADR (D2, D9-D12, the `UNVERIFIED (env)` boundary); create `deploy/hosting/` README stub; re-check open S1 items; update plan status to In progress. Check: docs link checker.
2. **Production overlay.** `deploy/hosting/compose.production.yml` and `.env.production.example`; add `/deploy/hosting` to Dependabot and confirm `check-pins` scans it. Check: A1, A2. Spike result for Compose `!reset`/`!override` support recorded.
3. **Verifier library and self-test.** `scripts/hosting_verify_lib.py`, `scripts/hosting-verify.sh` (assertions 1-9 plus `--config-only`), the planted-violation fake server tests. Check: A3, shellcheck.
4. **Edge project and rig, spike first.** `deploy/hosting/edge/` (compose, Caddyfile, ABB template with every requirement above, ServerFlow example) and `scripts/hosting-e2e.sh` bringing the overlay up under Colima with Caddy `tls internal` on `abb.localhost:8443`. Spike the https fake-issuer path and record the decision (full browser sign-in vs fallback). Check: stack healthy, `hosting-verify.sh` runs; failures listed as findings for step 5/6.
5. **Rig assertions green.** Fix configuration until A4-A6 pass; capture measured container memory under smoke load and set the `mem_limit` values; add the Playwright sign-in spec if the spike allowed it; `make hosting-e2e`. Record the evidence in the plan.
6. **Conditional: code or compose change.** Only if step 5 proves a gap (U2, metrics bucket, header conflict). Includes fail-before test, ADR amendment, security review note. If nothing is proven, commit a one-line plan note "no code change required" and the KI dispositions.
7. **Backup and restore drill scripts.** `hosting-backup.sh`, `hosting-restore-drill.sh`, systemd units, textfile metrics, KI-243 first-run check. Check: A7 against a local rclone remote, shellcheck.
8. **Host bootstrap, secrets and upgrade scripts.** `hosting-bootstrap.sh` (with `--check`), `hosting-secrets.sh`, `hosting-upgrade.sh`, `--server` assertions in the verifier. Check: A9, shellcheck, secrets script test (modes, no printing, no overwrite).
9. **Monitoring overlay and rules.** `observability.compose.yml`, `rules/host.yml`, Alertmanager config and runbook-URL template, node_exporter textfile wiring; extend `obs-e2e`-style check with promtool unit tests for the disk/memory/backup rules. Check: A8; KI-244/245 dispositions.
10. **Hosting guide and runbook.** `docs/hosting-guide.md` (sizing and cost table with the "confirm" caveat, accounts, DNS, Google OAuth steps, B2 steps, bootstrap, first deploy order, verify, ServerFlow as second app, resize path, Vercel and Coolify notes, split-deploy variant, five dependency questions) and `docs/runbooks/hosting-operations.md`; update `docs/deployment.md`, `OPERATIONS.md`, `runbooks/README.md`, `backup-restore.md`, README link. Check: A2, A10.
11. **Housekeeping.** KI register (close KI-188; update KI-220/222/243/244/245; new items), `IMPLEMENTATION_PLAN.md` Phase 23 status, `PROJECT_STATE.md`, `make hosting-e2e` in CI as an optional or nightly job if runtime allows (decision recorded), `scripts/quality.sh quick` and `full`. Review pass (`review-change`) with a security note, then `harden-change`.
12. **PR preparation** (needs the owner's go-ahead to push; never merge).
- **G1 (owner-gated): provision.** The owner provides and pays for: Hetzner account and server (region **[NEEDS INPUT]**), domain and DNS records, Google OAuth client, Backblaze B2 (or Storage Box) account and bucket/key, the chosen alert receiver and uptime check. Then run bootstrap, secrets, first deploy in the order above. The lead may drive SSH while the owner is present or provide commands; the agent never types or sees passwords, keys or tokens.
- **G2 (owner-gated): live verification.** `hosting-verify.sh https://abb.<domain> --server`, the Let's Encrypt and Google sign-in checks, a real restore drill from B2, a forced alert, then switch `ABB_RETENTION_MODE=enforce` only after the drill. Close A12/A13, move the plan to `completed/`.

## [NEEDS INPUT]

1. Hetzner region (EU Germany/Finland cheapest; US available).
2. The domain name (and registrar/DNS host).
3. Second backup provider: Backblaze B2 (recommended) or Hetzner Storage Box.
4. Uptime-check preference (external free tier, or none).
5. Alert receiver (email, Slack, webhook) for Alertmanager.
6. Public repository address is still open from Phase 20 (affects `runbook_url` rewrite).

## Open questions

- **U1** Can the https fake issuer meet the production https-issuer rule (CA mount and network alias) without code change? Recommendation: spike in step 4; fall back to asserting sign-in in the existing https e2e.
- **U2** Web read proxy drops browser `X-Forwarded-*`, so API throttles on session traffic see the web container. Recommendation: accept (SDK ingestion, the high-volume path, is direct and per-address); revisit with a justified change only if the rig shows a user-visible gap.
- **U3** Single hostname with path split (D9) versus `api.<domain>`. Recommendation: single hostname.
- **U4** Keep HTTP/3 (UDP 443)? Recommendation: off at first (smaller attack and firewall surface).

## Progress log

Owner accepted U1-U4 and D9-D14 as recommended (2026-10-19). Open S1 items re-checked in step 1: `docs/KNOWN_ISSUES.md` lists none open (KI-018, KI-019 resolved in 19a).

- Step 1 (ADR-178 and skeleton): VERIFIED. `python3 scripts/check_docs_links.py README.md docs site CONTRIBUTING.md` -> `checked 215 Markdown files: 0 broken link(s)`.
- Step 2 (production overlay): VERIFIED. `docker compose --env-file <dummy> -f docker-compose.yml -f deploy/hosting/compose.production.yml --profile app --profile observability --profile ops config --format json` renders no `ports` on postgres, api, web, migrate, worker, retention, backup; only prometheus/grafana on `127.0.0.1`; `ABB_ENVIRONMENT=production` on api, migrate, worker, retention, backup; `fake-oidc` absent from every profile; api and web also on `edge` with aliases `abb-api`/`abb-web`. `!reset`, `!override` and the profile override work on Compose 5.6.0 (spike result; the minimum documented is 2.24). Missing secrets stop `config` (required-variable interpolation). Finding: Compose interpolates every service, including inactive ones, so a required variable used only by the dashboards would block the app stack; the Grafana password therefore moves to the hosting observability overlay (step 9). `scripts/check-pins.sh` OK, and a planted `image: caddy:2` in `deploy/hosting/edge/compose.yml` fails it (`image without @sha256 digest`), so the scan covers the new tree. Dependabot: `directories: [/, /deploy/hosting, /deploy/hosting/edge]` for `docker-compose`. `.gitignore` unignores `.env.production.example` (the `.env.*` rule hid it). `scripts/docs-commands-check.sh` 0 problems; `python3 -m unittest discover -s scripts/tests` 53 tests OK. Note for step 9: name the observability overlay `compose.observability.yml` so `check-pins.sh` (regex `compose...`) and Dependabot see it.
- Step 3 (verifier and self-test): VERIFIED. `python3 -m unittest scripts/tests/test_hosting_verify.py` -> `Ran 41 tests ... OK` (about 20 s): a well-behaved fake origin passes every assertion, and 30 planted defects (no redirect, missing/duplicated HSTS, constant nonce, `unsafe-inline`, nonce not on the page, no nosniff, `X-Powered-By`, `Server` version, cacheable API, public `/docs`, `/readyz`, `/metrics`, `/metrics` with the token, cookie without Secure/HttpOnly or with Domain/wrong Path, no gzip, no 413, 413 too low, buffered stream, no heartbeat, gzip stream, forged `X-Forwarded-For` trusted, no throttle, no Retry-After, no sign-in flood limit, health down in the flood) each turn exactly the matching assertion red; config mutations (ports, environment, fake-oidc, dev passwords, trust proxy, http origin, edge network, limits) and a wide-open `docker inspect` port are caught; the real overlay passes `--config-only` and the development file alone fails it; no cookie value, token or `abb_live_` string reaches the output. `scripts/hosting-verify.sh --config-only --env-file <test env>` -> `HOSTING-VERIFY: 7 passed, 0 failed, 0 skipped`. shellcheck is not installed: run with the pinned image `koalaman/shellcheck@sha256:2097951f...` (7 MB): `scripts/hosting-verify.sh` clean. `check-pins`, docs links, docs commands, release checklist, `unittest discover -s scripts/tests` (94 tests) all pass. The lib is Python 3.9 compatible (the machine's `python3`). Design notes: the lib speaks raw sockets for the stream probe (no layer can buffer on its behalf); the web sign-in limiter answers a redirect with `Retry-After`, not a 429, so the flood check accepts that shape; the 413 check sends an unauthenticated body so the API never reads it and a 413 can only come from the proxy; sign-in via the fake issuer is added in steps 4-5 after the spike.
- Step 4 (edge project, rig, spike): VERIFIED by `scripts/hosting-e2e.sh` (below). Built: `deploy/hosting/edge/` (`compose.yml` with `caddy:2.11-alpine@sha256:d8542f48...`, `Caddyfile`, `abb.caddy.template`, `serverflow.caddy.example`), `deploy/hosting/rig/` (app overlay, edge overlay, `local_certs` global, the fake issuer's site, a negative-control overlay), `scripts/hosting-e2e.sh` (`make hosting-e2e`), `scripts/hosting_e2e_seed.py`, `make hosting-verify URL=...`. The rig runs on ports 18080/18443 under its own compose projects (`abb-hosting-rig`, `abb-hosting-rig-edge`), network and generated secrets, images tagged `abb-hosting-rig-*` so a developer's `agent-black-box-*` tags are never moved.
  **Spike U1 (answered, no code change):** the fake issuer runs as an https issuer `https://oidc.abb.localhost:18443` behind the same Caddy; the API (ABB_ENVIRONMENT=production) reaches it through a network alias on Caddy and trusts Caddy's local CA because the rig mounts a combined CA bundle over certifi's `cacert.pem` (the OIDC client ignores `SSL_CERT_FILE` on purpose: `trust_env=False`). Production's https-issuer rule is untouched. Full sign-in (login start, provider form, callback, session cookie, `/v1/me`) and a refused stranger run in `hosting-verify.sh` at the HTTP level; no browser is involved, so no Playwright spec was added (the existing `auth-https-e2e.sh` remains the browser check of the cookie behaviour; JavaScript execution of the dashboard behind Caddy is not exercised).
  **Findings from the first runs (all fixed in config or in the rig; none needed a change under `apps/` or `packages/`):** (1) Colima shares only the home directory with its VM: a bind mount from `/var/folders` or `/tmp` mounted an empty directory silently (Caddy saw no sites, the CA bundle mount failed), so the rig keeps its work directory under the checkout (`.local/`, git-ignored). (2) Caddy's `request_body { max_size }` fires only when something reads the body: an unauthenticated 9 MiB POST reached the API and got a 401. Fixed with a `Content-Length` guard (`@oversize` CEL matcher, `respond 413`) in front of the proxy, keeping `max_size` for chunked bodies; both verified with a standalone Caddy (declared and chunked bodies over 8 MiB: 413; 7 MiB: 200). (3) The correct whoami route is `/v1/me`, not `/v1/auth/me`. (4) Relay reads need `X-ABB-Workspace: ws_...` (400 without it). (5) The web sign-in limiter answers a redirect carrying `Retry-After`, not a 429; the flood assertion accepts that shape. (6) `docker compose` interpolates every service, so a required variable used only by the dashboards would block the app stack (the Grafana password moved to step 9's overlay).
- Step 5 (rig assertions green; limits from measurements): VERIFIED. `scripts/hosting-e2e.sh` ends `HOSTING-E2E PASSED`; `scripts/hosting-verify.sh` against `https://abb.localhost:18443` (real Caddy, local CA, production-mode overlay): `HOSTING-VERIFY: 30 passed, 0 failed, 0 skipped`; `--config-only`: 7 passed; `--docker-project`: `ports.none_published  6 container(s), no host binding other than loopback`; rig-only: `scrape.in_network  /metrics with the token returned 59 metric families over the private network`, `network.postgres_not_on_edge`, `log.access_log_clean  178 access-log lines, no header, cookie, key or query string`. The negative control (`HOSTING_E2E_NEGATIVE=1`: trust proxy off, a naive proxy that routes `/metrics` and `/readyz` to the API, gzips everything and limits no body) ends `HOSTING-E2E NEGATIVE CONTROL PASSED`: the verifier turned red on `paths.docs_readyz_closed`, `paths.metrics_not_public`, `body.over_limit_refused_413`, `sse.not_compressed`, `xff.forged_header_cannot_evade_throttle`, `xff.throttle_has_retry_after` and `flood.signin_start_limited` against the real stack (Caddy's gzip did not stall the stream: `sse.heartbeat` stayed green, so the assertion that matters there is the encoding one).
  **Memory (measured, `docker stats` sampled every few seconds while the rig ran its 2,040-event smoke load, five renders of the main dashboard pages, 620 junk session cookies, the sign-in flows and the verifier; peak MiB of the last positive run):** api 166, worker 87, web 72, retention 68, postgres 58, caddy 16 (the fake issuer 39 is rig-only); sum of peaks about 470 MiB. Limits set in `deploy/hosting/compose.production.yml`: postgres 512m (room for `shared_buffers` 128 MB to fill and for growth), api 768m (the measured baseline plus the worst measured case of the body-bytes budget, `ABB_MAX_BODY_BYTES_IN_FLIGHT` 64 MiB x about 6.5, docs/OPERATIONS.md), worker 384m, web 384m, retention 256m (dry_run; revisit before enforce), migrate 512m (one-shot), caddy 128m. Ceilings sum to about 2.4 GiB against about 0.5 GiB actually used; ServerFlow measured 0.73 GiB idle, so the pair idles near 1.3 GiB on the 3.8 GiB (4 GB class) machine the plan targets. Caveat: these are small-data numbers; PostgreSQL and the web server grow with data and use, hence the headroom and the memory alerts planned for step 9.
