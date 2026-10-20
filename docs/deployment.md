# Deployment on one machine

How to run the Agent BlackBox stack on a single machine that other people or other machines can reach, using the Docker
Compose file in the repository. Read the [quickstart](quickstart.md) first: it is the same stack with development settings.

## Scope and status

This page covers one machine, one Compose project, one PostgreSQL. It does **not** cover:

- a domain name, TLS certificates or a reverse proxy beyond the notes [below](#behind-a-reverse-proxy) (the [hosting guide](hosting-guide.md) covers them for one server);
- setting up an identity provider (the sign-in runbook covers the settings; you bring the provider);
- more than one machine, a managed database, high availability or autoscaling;
- hosting a service for other organisations. Nothing in the project is operated for you.

For a rented server with a domain, automatic HTTPS, Google sign-in, off-site backups and monitoring, follow the [hosting guide](hosting-guide.md) instead: it is the end-to-end
recipe (Phase 23, [ADR-178](decisions/ADR-178-private-hosting-topology.md)) and its proxy chain is exercised locally by `make hosting-e2e`. This page remains the shorter reference for running
the stack behind a proxy you operate yourself.

**What has been verified.** The settings, commands and file names on this page are checked against the repository by
`scripts/docs-commands-check.sh` (every variable, `make` target, Compose profile and path exists). The Compose stack itself runs
in CI (`containers` job) with development settings. The production overrides below have **not** been run end to end on a clean
machine by the project: treat them as a checklist to follow and test, not as a certified recipe.

## What the stack contains

`docker-compose.yml` defines these services. The `app` profile is the product; `ops` and `observability` are optional.

| Service | Profile | What it is | Published on the host |
| --- | --- | --- | --- |
| `postgres` | (always) | PostgreSQL 16, the only system of record | `127.0.0.1:5433` |
| `migrate` | `app` | Applies database migrations and exits | none |
| `api` | `app` | The HTTP API: ingestion, queries, live streams | `127.0.0.1:8000` |
| `worker` | `app` | Background jobs: run summaries, costs, findings, evaluations, alerts | none |
| `retention` | `app` | The data-retention job; dry run unless you turn enforcement on | none |
| `web` | `app` | The dashboard (Next.js) | `127.0.0.1:3000` |
| `fake-oidc` | `app` | A development-only sign-in provider that accepts any email | shares the API container's network |
| `backup` | `ops` | One-shot backup, verify and restore tool | none |
| `prometheus`, `grafana` | `observability` | Local metrics and dashboards | `127.0.0.1:9090`, `127.0.0.1:3001` |

Everything is published on `127.0.0.1` only. To make the dashboard reachable, put a reverse proxy in front of it; do not
change the published addresses to `0.0.0.0`.

Data lives in two Docker volumes: `pgdata` (the database) and `artifacts` (diffs, terminal output and other large bodies,
mounted by the API, the retention job and the backup service). Anything else is rebuildable from the repository.

## Change these before anyone else can reach it

The shipped file is a development stack. These are the defaults that must not survive into a deployment.

| Setting | Development default | What to do |
| --- | --- | --- |
| `ABB_ENVIRONMENT` | `development`, fixed in the Compose file | Set `production` for `migrate`, `api`, `worker`, `retention` and `backup`. It switches off the `http` issuer, dev sessions and the seeded `owner@local.test`, hides `/docs`, and makes the API refuse to start as a database role that can rewrite events. |
| `fake-oidc` service | started with the `app` profile | Do not start it (start services by name, below). Configure a real provider with `ABB_OIDC_ISSUER` (https), `ABB_OIDC_CLIENT_ID`, `ABB_OIDC_CLIENT_SECRET`. See [sign-in, roles and access](runbooks/auth-and-access.md). |
| `ABB_ALLOW_DEV_SESSIONS` | unset | Never set it in a deployment. |
| `ABB_POSTGRES_PASSWORD` | `abb_dev_password` | Set a generated value **before the first start**: PostgreSQL stores it when it first creates the volume. To change it later, `ALTER ROLE abb PASSWORD ...` in the database and update the variable. |
| `ABB_RUNTIME_DB_PASSWORD` | `abb_runtime_dev_password` | Set a generated value. The `migrate` service applies it to the `abb_runtime` role on every start; this is the role the API and worker connect as. |
| `ABB_RETENTION_DB_PASSWORD` | `abb_retention_dev_password` | Set a generated value (the `abb_retention` role is the only one that may delete retained data). |
| `ABB_METRICS_TOKEN` | unset (`/metrics` does not exist) | Set a token of at least 24 characters if you scrape metrics. Never expose the endpoint publicly. |
| `ABB_GRAFANA_ADMIN_PASSWORD` | `abb_grafana_dev_password` | Only if you run the `observability` profile. |
| `ABB_WEB_ORIGIN` | `http://localhost:3000` in the Compose file | The public `https://` origin of the dashboard, set on `api` and `web`. It also makes the session cookies `Secure`. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000`, a **build** argument of `web` | The public API address that browsers and SDKs use, if it differs. Rebuild `web` after changing it. |
| `ABB_CORS_ORIGINS` | `http://localhost:3000` | The same origin as `ABB_WEB_ORIGIN`. |

### Generate the secrets

Use long random values and keep them in a secret store or a file readable only by the account that runs Compose. Do not commit
them and do not paste them into chat or tickets.

<!-- check:run -->
```bash
echo "ABB_POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "ABB_RUNTIME_DB_PASSWORD=$(openssl rand -hex 24)"
echo "ABB_RETENTION_DB_PASSWORD=$(openssl rand -hex 24)"
echo "ABB_METRICS_TOKEN=$(openssl rand -hex 32)"
```

Put the lines you need in the `.env` file next to `docker-compose.yml` (Compose reads it for the `${...}` values in the file) or
in your deployment tool's secret mechanism. The other secrets (the identity provider's client secret, SMTP password, backup
passphrase) are listed with their rotation steps in [secrets](runbooks/secrets.md).

### An override file

Compose merges a second file over the first. This one sets the environment and the public origins and leaves the project's
own file untouched. Replace the example host names, then test it on a copy before you rely on it.

```yaml
# docker-compose.production.yml (yours; not part of the repository)
services:
  migrate:
    environment:
      ABB_ENVIRONMENT: production
  api:
    environment:
      ABB_ENVIRONMENT: production
      ABB_WEB_ORIGIN: https://abb.example.org
      ABB_CORS_ORIGINS: https://abb.example.org
  worker:
    environment:
      ABB_ENVIRONMENT: production
  retention:
    environment:
      ABB_ENVIRONMENT: production
  web:
    build:
      args:
        NEXT_PUBLIC_API_URL: https://api.example.org
    environment:
      ABB_WEB_ORIGIN: https://abb.example.org
```

The identity provider settings (`ABB_OIDC_ISSUER`, `ABB_OIDC_CLIENT_ID`, `ABB_OIDC_CLIENT_SECRET`) are read by the Compose file from
`.env`; put them there, with the secret never in the override file.

## Start it

Start the services by name so the development sign-in provider is not started:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile app up -d --build --wait postgres migrate api worker retention web
```

The `migrate` service applies all migrations before the API and worker start. Check that it is ready:

```bash
curl -s localhost:8000/readyz
```

`/healthz` is the liveness probe (no dependencies); `/readyz` is the one to gate traffic on. Then create your first workspace
and owner with the commands in [sign-in, roles and access](runbooks/auth-and-access.md#2-the-first-owner-and-everyone-after):
in a deployment nobody is seeded, and a person who signs in without an invitation sees no workspaces.

## Upgrade

1. Take a backup (below) and verify it.
2. Pull the new version of the repository, read the release notes in [project state](PROJECT_STATE.md) for deploy notes, and
   run the same `up -d --build --wait` command. Migrations run first.
3. Check `/readyz` and open a run.

Rolling back a migration, and which migrations cannot be rolled back, is covered in [migrations](runbooks/migrations.md).
Deploy the API and the web app together: a version mismatch breaks sign-ins that were in flight.

## Backups

A backup bundle holds the whole database (including the keys that seal alert secrets) and the artifact files, so it is as
sensitive as the database: encrypt it. Make one from the host (`pg_dump` and `pg_restore` must be on the PATH, at least as new as the server):

```bash
make backup OUT=/var/backups/abb/2026-01-01 BACKUP_FLAGS="--encrypt --passphrase-file /etc/abb/backup.pass"
make backup-verify BUNDLE=/var/backups/abb/2026-01-01 BACKUP_FLAGS="--passphrase-file /etc/abb/backup.pass"
```

The Compose `ops` profile has a `backup` service that does the same inside a container; its command and the directory
permissions it needs are in the runbook below.

Scheduling, restore, the restore drill and what a bundle does not contain are in the
[backup and restore runbook](runbooks/backup-restore.md). Practise a restore into a scratch database before you depend on
the backup.

## Retention

By default the `retention` service runs in `dry_run` mode: it measures and reports what it would delete and deletes nothing.
Setting `ABB_RETENTION_MODE=enforce` deletes expired runs and artifacts **permanently**. Take and restore-test a backup first,
read the reports for a full cycle, then switch. Workspace owners set the days per tier in Settings, Retention. See
[retention](runbooks/retention.md) and [operations](OPERATIONS.md#data-retention-phase-19b-steps-1-2-adr-159-adr-160).

## Metrics and logs

With `ABB_METRICS_TOKEN` set, the API serves `GET /metrics` for a Prometheus scrape (`Authorization: Bearer <token>`); without
it the route does not exist. The worker serves its own when `ABB_WORKER_METRICS_PORT` is set. Logs are structured, one JSON
object per line, and never contain event payloads or secrets. The shipped alert rules and Grafana dashboard run locally with
`make observability-up`; how to scrape and what to alert on is in [operations](OPERATIONS.md#metrics-phase-19c-adr-165) and
the [runbooks](runbooks/README.md).

## Behind a reverse proxy

The stack speaks plain HTTP on loopback; terminate TLS at a proxy you run. The essentials (the full list is in
[operations](OPERATIONS.md#edge-headers-docs-body-cap-and-running-behind-a-reverse-proxy-phase-19a-step-4-adr-155)):

- Set `ABB_WEB_ORIGIN` to the public `https://` origin. That turns on `Secure` cookies and the HSTS header.
- Set `ABB_TRUST_PROXY=1` **only** if the proxy is the sole way to reach the API and web ports and it appends the client
  address as the last `X-Forwarded-For` entry. Left at 0 the header is ignored, which is safe but means failed sign-ins are
  throttled globally rather than per address.
- Do not cache HTML or strip the security headers: each page carries a per-request nonce.
- Disable response buffering and allow read timeouts above 15 s for `text/event-stream`, or the live run page will not update.
- Limit request bodies to 8 MiB and add your own per-address rate limit; the API's limits are a backstop, not a firewall.

## Not covered

High availability, more than one machine and a managed database are not covered. Domain and DNS, certificate issuance, Google sign-in and host monitoring for one
server are in the [hosting guide](hosting-guide.md); its live parts (Let's Encrypt, Google, the second backup provider, the server's firewall) are `UNVERIFIED (env)` until you
have run them. Another identity provider, or a different proxy, is yours to arrange with [operations](OPERATIONS.md), the [runbooks](runbooks/README.md) and
[security](SECURITY.md) as the reference.
