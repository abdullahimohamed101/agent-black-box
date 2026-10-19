# Troubleshooting

When something does not work on a first run, find the symptom below. Each entry gives the likely cause, a command that
confirms it, and the fix. For a running installation (alerts, queues, databases under load) use the
[runbooks](runbooks/README.md) instead; for the quickstart itself see the [quickstart](quickstart.md).

Agent BlackBox keeps its local state in three places: the Compose containers and volumes, the `.env` file in the repository
root, and `.local/` (the development API key). Most first-run problems are one of those three being missing or out of date.

## Contents

- [`make up` fails or `docker` is not found](#docker-is-not-running)
- [A port is already in use](#a-port-is-already-in-use)
- [The page still shows old behaviour after an update](#old-images-after-an-update)
- [`make` says `.env` does not exist, or a variable is unset](#env-is-missing-or-a-variable-is-unset)
- [The stack is slow, restarts, or is killed (Colima and memory)](#colima-and-memory)
- [`make seed` fails](#make-seed-fails)
- [The agent ran but the dashboard shows nothing](#the-dashboard-is-empty)
- [A run stays in `processing`](#a-run-stays-in-processing)
- [`401` or `403` from the API](#401-or-403-from-the-api)
- [`429` rate limit or quota refusals](#429-rate-limits-and-quotas)
- [Sign-in loops, or "no workspaces"](#sign-in-problems)
- [The run page does not update live](#the-run-page-does-not-update-live)
- [Diffs and terminal output say "not available"](#diffs-or-terminal-output-are-missing)
- [Python or Node version errors](#python-or-node-version)

## Docker is not running

**Symptom.** `make up` or `make db` prints `Cannot connect to the Docker daemon`, or `docker: command not found`.

**Cause.** Compose needs a running Docker engine. On macOS this project is developed with Colima.

```bash
docker info
```

**Fix.** Start your engine (Docker Desktop, or `colima start --cpu 2 --memory 4`). If `docker compose` is "not a docker
command" on macOS with Colima, Compose is installed but not registered: add
`"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` to `~/.docker/config.json`
([setup notes](development/setup.md#macos-notes)).

## A port is already in use

**Symptom.** `make up` fails with `address already in use` or `port is already allocated`.

**Cause.** The stack publishes these ports on `127.0.0.1` only:

| Port | Service |
| --- | --- |
| 3000 | web |
| 8000 | API |
| 8900 | the development sign-in provider (inside the API container's network) |
| 5433 | PostgreSQL (5433, not 5432, so it never clashes with a database you already run) |
| 3001, 9090 | Grafana and Prometheus, only with `make observability-up` |

Find what holds a port:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

**Fix.** Stop the other process. A common culprit is a native `make dev` still running while you start `make up` (both use
3000 and 8000): stop one of them. A previous stack that was never stopped is `make down`.

## Old images after an update

**Symptom.** After `git pull` the dashboard or API behaves like the previous version, or a new page returns 404.

**Cause.** The containers were built from the old source and are being reused.

**Fix.** Rebuild and recreate. `make up` already passes `--build`, so run it again; if it still looks stale, remove the old
containers first (this keeps your data volumes):

```bash
make down
make up
```

A database schema that is behind the code shows up as the API failing its readiness check; the `migrate` service applies
migrations on every `make up`. To see its result: `docker compose --profile app logs migrate`.

## .env is missing or a variable is unset

**Symptom.** `make seed` or `scripts/quality.sh` prints `.env: No such file or directory` or
`ABB_DATABASE_URL is not set; copy .env.example to .env`.

**Cause.** `.env` is not committed (it is git-ignored on purpose). It is created from the example.

**Fix.**

```bash
cp .env.example .env
```

The example holds development values only. Do not put real secrets in a file you intend to commit, and do not reuse its
passwords anywhere reachable by others ([deployment](deployment.md#change-these-before-anyone-else-can-reach-it)).
`make` loads `.env` with the shell, so a value containing spaces, `$` or `#` must be quoted (KI-013).

## Colima and memory

**Symptom.** The build is very slow, a container exits with code 137, or `docker compose` hangs on the web image build.

**Cause.** The Colima virtual machine has too little CPU or memory for building the web image and running PostgreSQL, the
API, the worker and the web server together. Exit code 137 is an out-of-memory kill.

```bash
colima list
docker stats --no-stream
```

**Fix.** Give the VM more: `colima stop && colima start --cpu 4 --memory 6`, then `make up` again. The setup notes use
2 CPUs and 4 GB as a minimum. Close other containers first if you cannot raise it.

## make seed fails

**Symptom.** `make seed` prints a database connection error.

**Cause.** `make seed` runs on your machine (not in a container) and connects to the PostgreSQL that Compose publishes on
host port 5433. Either the database is not up yet, or `ABB_DATABASE_URL` in `.env` points elsewhere.

```bash
docker compose ps postgres
```

**Fix.** Run `make up` (or `make db` for the database alone) and wait for it to report healthy, then `make seed`. Keep the
default `ABB_DATABASE_URL` from `.env.example` unless you changed the published port. `make seed` is safe to repeat: it
keeps a still-valid key in `.local/dev-api-key`.

It refuses to run when `ABB_ENVIRONMENT=production`; it is a development convenience.

## The dashboard is empty

**Symptom.** Your agent finished without an error, but the dashboard shows no runs.

Check these in order:

1. **Is the SDK sending anything?** The SDK never raises into your agent, so a wrong endpoint or key fails quietly. Print
   its counters at the end of your script: `print(bb.stats())` shows what was sent, dropped and rejected. Without an API key
   the client downgrades to `disabled` with one warning.
2. **Right endpoint?** From the host the API is `http://localhost:8000` (`BLACKBOX_ENDPOINT`); check `curl -s localhost:8000/readyz`.
3. **Right project?** A key belongs to one project. `make seed` creates the project `demo` in the workspace `local`; a key
   from another project writes runs you will find in that project's page, not in `demo`. The dashboard's project list shows
   them all.
4. **Signed in as someone who can see it?** Sign in as `owner@local.test` (see below). Another email sees "no workspaces" until
   invited.
5. **Filters.** Clear any filter chips or search text on the runs page.

## A run stays in processing

**Symptom.** The run appears but its status, cost and findings never settle.

**Cause.** Summaries are computed by the worker, a separate process. `make up` starts it. A native `make dev` does not start
a worker.

```bash
docker compose --profile app ps worker
```

**Fix.** Use `make up`, or start the worker natively (`cd apps/api && uv run python -m abb_api.worker`, with `.env`
exported). If it is running and runs still do not settle, see the [worker lag runbook](runbooks/worker-lag.md).

## 401 or 403 from the API

| Response | Meaning | Fix |
| --- | --- | --- |
| `401 API_KEY_INVALID` | The key is missing, mistyped, revoked or expired. The same answer is given for all of these on purpose. | Send `Authorization: Bearer <key>`. For the development key: `$(cat .local/dev-api-key)`. Run `make seed` to issue a new one. |
| `401 SESSION_INVALID` | You called a dashboard route with an expired or missing session cookie. | Sign in again. |
| `403 INSUFFICIENT_SCOPE` | The key is valid but lacks a scope. The response names it in `details.required_scope`. | The seed key holds `events:write` and `runs:read`. Uploading diffs and terminal output needs `artifacts:write`; create one with the command in the [quickstart](quickstart.md#see-a-failed-run). |

Details for every error code: [API reference](architecture/api-v1.md).

## 429 rate limits and quotas

**Symptom.** The SDK reports dropped events, or `curl` returns `429`.

`429 RATE_LIMITED` is a rate limit; honour `Retry-After`. `QUOTA_EXCEEDED` means a per-project limit (agents, runs or events
per day, artifact bytes) was reached: what the project already has keeps working, the excess is refused. Neither hides data
silently: the response says which limit. Look at the project's usage in Settings, Quotas, and see
[abuse resistance](runbooks/hardening.md) and [ingestion rejections](runbooks/ingestion-rejections.md) for the full table.
A development stack with a noisy script usually hits the rate limit; lower the send rate or raise
`ABB_RATE_LIMIT_EVENTS_PER_SECOND` and `ABB_RATE_LIMIT_BURST_EVENTS` (see [OPERATIONS](OPERATIONS.md)).

## Sign-in problems

The development stack signs you in through a fake provider that accepts any email. It exists only under
`ABB_ENVIRONMENT=development` or `test`.

- **"No workspaces" after signing in.** You signed in with an email that is not a member. Use `owner@local.test`, which
  `make seed` creates, or add yours:
  `docker compose --profile app exec -T api python -m abb_api.cli add-member --workspace local --email you@example.com --role VIEWER`.
- **A redirect loop or "Sign-in did not complete".** Cookies are tied to the origin you used. Open
  `http://localhost:3000` (not `127.0.0.1:3000`) and make sure `ABB_WEB_ORIGIN` matches what is in the address bar. If you
  switched between `make dev` and `make up`, clear the site's cookies.
- **You want a real identity provider.** See [sign-in, roles and access](runbooks/auth-and-access.md). The provider must send
  `email_verified: true`.

## The run page does not update live

**Symptom.** You must reload to see new events.

**Cause.** The page follows a run over a server-sent-events stream. Something between the browser and the API buffers or
cuts idle connections (a reverse proxy, a corporate VPN), or the per-key stream limit is reached (`429 STREAM_LIMIT`).

**Fix.** Local setups have no proxy: reload and check the browser console. Behind a proxy, disable response buffering for
`text/event-stream` and set its read timeout above 15 s. Details: [live streams](runbooks/stream-issues.md).

## Diffs or terminal output are missing

**Symptom.** A run shows its steps, but a diff or shell output says "not available" or is empty.

**Cause.** Two things must both allow it. The key needs the `artifacts:write` scope (the seed key does not have it), and the
project must capture payloads. `make seed` makes the `demo` project capture full payloads; a project you create yourself
defaults to metadata only, and the server then drops payloads and artifact bodies even if the SDK sends them.

**Fix.** Use a key with `artifacts:write`, and set the project to full capture:

```bash
docker compose --profile app exec -T api python -m abb_api.cli set-capture-mode --workspace local --project demo --mode full
```

A viewer role never sees command text or file paths; that is by design ([security notes](SECURITY.md)).

## Python or Node version

**Symptom.** `uv sync` says no interpreter satisfies the requirement, or `pnpm install` refuses the Node version.

| Tool | Needed |
| --- | --- |
| Python | 3.12 for the repository (`uv` downloads and manages it); the SDK alone supports 3.10 and later |
| Node.js | 22 or later (`.nvmrc` says 22) |
| pnpm | the version in the root `package.json` (`packageManager`); Corepack or `pnpm/action-setup` provides it |
| Docker with Compose | a recent release |

If you only want to try the product, you need Docker and `uv`; Node is needed only to build or test the web app natively.

## Still stuck

Open an issue (see [CONTRIBUTING](../CONTRIBUTING.md)) with the output of `docker compose --profile app ps` and the last
lines of `docker compose --profile app logs api worker`. Logs never contain event payloads or secrets by design, but check
before pasting.
