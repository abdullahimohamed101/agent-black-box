# Phase 23 review: private hosting (adversarial, read-only)

Date: 2026-10-19. Branch `feature/phase-23-hosting` at `178ca4e`, diff `origin/main...HEAD` (71 files). Reviewed by reading every
file under `deploy/hosting/`, `scripts/hosting-*.sh`, `scripts/hosting_*.py`, the systemd units, the guide, the runbook, ADR-178 and
the plan, plus the API/web code the claims depend on (`apps/web/src/server/upstream.ts`, `apps/api/src/abb_api/core/client_address.py`,
`apps/api/src/abb_api/backup/restore.py`, `docker-compose.yml`).

What was run here (VERIFIED): `python3 -m unittest discover -s scripts/tests` (147 tests OK, 3 skipped); `docker compose ... config
--format json` of the production overlay with a dummy env file (to list image names and ports); `scripts/docs-commands-check.sh`
(0 problems), `scripts/check-pins.sh` (OK), `python3 scripts/check_docs_links.py ...` (0 broken). Not run: the Docker rigs, shellcheck
(not installed; the plan ran it through a pinned image), anything against a real server. No `.env` or secret value was read.

Verdict: no P0. Five P1 items, all fixable in configuration or documentation; the Caddy front door, the secrets handling, the restore
drill's safety rules and the prune logic hold up. The branch should not merge until P1-1 to P1-5 are fixed, because two of them are
data-safety paths in the upgrade script and one is a regression of `main`'s known-issues register.

## P0 (critical)

None found.

## P1 (must fix before merge)

### P1-1 Rollback never restores the `web` image

- `scripts/hosting-upgrade.sh:83` (`built_images`), `:116-118` (tag `:rollback`), `:129` (restore), `:154-156` (tag `sha-`).
- `built_images()` keeps only services with both `build` and `image`. `docker-compose.yml` gives `web` a `build:` but no `image:`
  (verified with `docker compose ... config --format json`: `web image= None build= True`; every other built service renders
  `agent-black-box-api:local`). So `web` is never tagged `:rollback` or `sha-<rev>`.
- Failure: an upgrade builds a new `agent-black-box-web` image, `api` fails its health check, the schema is unchanged, the script says
  `ROLLED BACK` and restarts `web` from the image Compose finds by default name, which is the NEW build. The previous release now runs
  the old API with the new dashboard (whose server-side relay and page code may not match the old `/v1` contract); the message claims
  the opposite. There is also no way to roll the web image back by hand, since nothing kept it.
- Fix: give the web service an explicit image name in the production overlay (`image: agent-black-box-web:local`, mirroring the API)
  so the existing tag/restore loops cover it, and make `hosting-upgrade-e2e.sh` assert that the web image id after rollback equals the
  pre-upgrade id (U3 currently proves it for the API only).

### P1-2 A stopped PostgreSQL is mistaken for a first deploy: no backup, no rollback

- `scripts/hosting-upgrade.sh:81` (`revision()` swallows every error), `:86-87` (`FIRST=1` when the query returns nothing),
  `:103-104` (no backup on "first deploy"), `:123-125` (`rollback_or_report` gives up on "first deploy").
- `revision()` runs `dc exec -T postgres psql ...` and returns an empty string whenever the container is not running, not only when
  `alembic_version` is absent. A server whose stack was stopped (`docker compose stop`, a troubleshooting `down` without `-v`, Docker
  restarted without live-restore, a reboot where `unless-stopped` did not apply because someone had stopped a service) has a full
  database and no running container.
- Failure: the operator runs `hosting-upgrade.sh v1.5.0` on that server. The script logs `first deploy: no database yet, no backup to
  take`, builds, starts PostgreSQL in step 5, runs migrations on the populated database, and if anything fails it dies with `first
  deploy failed; nothing to roll back to`. The one upgrade that most needs a restore point has none, and the automatic rollback is
  disabled on the same run.
- Fix: in preflight, `dc up -d --no-build --wait postgres` before computing the revision (the volume is created only if absent, which
  is harmless); make a failed `psql` fatal (`die`) instead of silent; decide "first deploy" from the absence of the data volume
  (`docker volume inspect <project>_pgdata`) AND an empty `alembic_version`, never from a failed query. Add a rig case U7: stack
  stopped, upgrade must still take a backup.

### P1-3 Every upgrade after step 9 silently removes the worker's metrics port (monitoring regresses, then pages)

- `scripts/hosting-upgrade.sh:54` and `scripts/hosting-backup.sh:49`, `scripts/hosting-restore-drill.sh:46` default
  `ABB_COMPOSE_FILES` to `docker-compose.yml deploy/hosting/compose.production.yml`; `deploy/hosting/compose.observability.yml:186-189`
  sets `ABB_WORKER_METRICS_PORT`/`BIND` on `worker`; `docs/hosting-guide.md:230-231` starts the stack with the observability overlay;
  `docs/runbooks/hosting-operations.md:9` defines `$dc` without it. `ABB_COMPOSE_FILES` is not mentioned in `backup.env.example`, the
  guide, the runbook or `.env.production.example` (grep: only the three scripts).
- Failure: the operator follows the guide (step 9 recreates `worker` with the metrics port) and later runs `hosting-upgrade.sh`. Step 6
  `dc up -d --no-build --wait api worker retention web` renders the worker WITHOUT the overlay, sees a changed config, recreates it
  without the metrics listener. Prometheus' `abb-worker` target goes down: `AbbTargetDown` (severity page, `deploy/observability/
  prometheus/rules/ops.yml:23`) fires after every upgrade, and the worker's SLO series stop. The same happens to anyone using the
  runbook's `$dc up -d api worker` in the rotation table (`hosting-operations.md:121-122`). `hosting-verify.sh` does not notice.
- Fix: one source of truth for the file list. Either document `ABB_COMPOSE_FILES="docker-compose.yml deploy/hosting/compose.production.yml
  deploy/hosting/compose.observability.yml"` in `backup.env.example` (and have step 9 set it, and the runbook's `$dc` read it), or have
  the upgrade script add the observability overlay automatically when `secrets/grafana_admin_password` exists. Add `--profile
  observability` consistently. Add a rig assertion in `hosting-upgrade-e2e.sh`: after an upgrade with the overlay in use, the worker
  still has `ABB_WORKER_METRICS_PORT`.

### P1-4 The "Restoring onto a new server" procedure cannot be followed as written

- `docs/runbooks/hosting-operations.md:105-113`.
- Step 1 stops at guide steps 5-7 (no first deploy, so no PostgreSQL container and no volume). Step 2 runs `hosting-restore-drill.sh`,
  which needs the `postgres` container running (`scripts/hosting-restore-drill.sh:94`, `dc exec -T postgres`): it fails with "the
  database server is not reachable". Step 3 then assumes "a freshly migrated database" and says to point `ABB_DATABASE_URL`,
  `ABB_RETENTION_DATABASE_URL` and the artifact directory "at it in `.env`", but the database name `abb` is hard-coded in
  `deploy/hosting/compose.production.yml:63,74,96,104,114` (`.env` holds only the passwords); repointing means editing a tracked file,
  which dirties the checkout that `hosting-upgrade.sh:60` refuses. The artifact directory is a named volume, not a `.env` value either.
- Failure: the one procedure the owner reaches for after losing the server does not work at the first command, and its third step
  points at a knob that does not exist. This is the disaster-recovery path; being wrong here is a data-loss risk even though it is
  marked `UNVERIFIED (env)`.
- Fix (keeps the tool's "never a non-empty database" rule and needs no repointing): after guide steps 5-7, `$dc up -d --wait postgres`
  (a fresh volume creates an EMPTY `abb`), run the drill, then restore the bundle straight into `abb` with the artifact directory at the
  real volume (`backup` service, `--artifact-dir /data/artifacts`, which is empty on a fresh volume; `restore.py:_preflight_database`
  creates the missing `abb_runtime`/`abb_retention` roles itself since `abb` is the superuser), then `hosting-upgrade.sh <the tag the
  bundle came from> --no-verify` (the restored `alembic_version` makes it a normal deploy: it takes a backup, migrations are a no-op).
  Keep the `UNVERIFIED (env)` marker and KI-312, but make the text executable. Remove "freshly migrated" and the `.env` repoint.

### P1-5 `docs/KNOWN_ISSUES.md` regresses `main`: 24 filed-issue links reverted to "to file"

- `docs/KNOWN_ISSUES.md`, rows KI-272, 274, 275, 277, 278, 281-286, 288-291, 294, 295, 298-304 (24 rows). `main` (`ebbf88b docs: close
  Phase 20 ... known issues filed`) carries `[#301](https://github.com/.../issues/301)`-style links in the last column; the branch's
  merge commit `178ca4e` kept the older "to file" text for these rows. Compared with `comm` between `git show origin/main:docs/
  KNOWN_ISSUES.md` and the worktree file.
- Failure: merging the PR deletes the record of which issues were filed, undoing the Phase 20 close-out.
- Fix: re-resolve the table from `origin/main` and re-apply only the Phase 23 edits (KI-073/220/222/243/244/245 updates, KI-188 to
  Resolved, KI-310..315 new rows).

## P2 (should fix)

### P2-1 The shared `edge` network lets a second app bypass Caddy and forge client addresses

- `deploy/hosting/compose.production.yml:25-32` (api and web join `edge`), `:76,:125` (`ABB_TRUST_PROXY=1`), ADR-178 decision 3,
  `docs/hosting-guide.md:305-317` (ServerFlow's gateway joins the same `edge`), `apps/api/src/abb_api/core/client_address.py:17-22`
  (with trust on, the LAST `X-Forwarded-For` entry is believed, whoever sent it).
- Any container of the second app on `edge` can open `abb-api:8000` and `abb-web:3000` directly: no Caddy, so the closed paths are open
  from there (`/readyz`, `/metrics` and its global pre-token bucket, KI-220), and a self-supplied `X-Forwarded-For` is trusted, so the
  per-address throttles and the sign-in limiter can be evaded or used to frame another address. The plan names exactly this risk
  ("ABB_TRUST_PROXY=1 while a port is reachable directly") but the control it chose, "no published ports", does not cover other tenants
  of the shared network. Not reachable from the internet; it is a lateral-movement surface if ServerFlow (17 containers) is compromised.
- Fix: one edge network per app (`edge-abb`, `edge-sf`); Caddy joins all of them (`networks:` list in the edge compose), apps never
  share one. Bootstrap creates `edge-abb`; the ServerFlow example uses `edge-sf`. Update ADR-178 D10 and the guide's second-app
  section. Optionally `hosting-verify.sh --server` asserts that no container outside the project is attached to the project's edge
  network.

### P2-2 The guide asks for an AAAA record but the edge publishes on IPv4 only

- `deploy/hosting/edge/compose.yml:25-26` publishes `${ABB_EDGE_BIND:-0.0.0.0}:80:80` and `:443:443`; `docs/hosting-guide.md:65,78`
  (IPv6 on, `AAAA abb`). With an explicit `0.0.0.0` host address Docker binds the IPv4 socket only (an unspecified address is what gives
  the dual-stack `0.0.0.0` + `[::]` binding on current engines); `deploy/hosting/blackbox/blackbox.yml:12` forces `ip4`, so the probe
  would not notice.
- Failure: IPv6-capable clients and SDK hosts try the AAAA first and fall back only after a refused connection on every new connection
  (ufw passes 443 for v6 and nothing listens): slower, flaky under some resolvers, and Let's Encrypt validates over IPv6 first when an
  AAAA exists (it does retry over IPv4 for connection errors, so issuance usually succeeds, but the guide's "A and AAAA" is advertising a
  listener that does not exist).
- Fix: either publish both (`"[::]:80:80"`, `"[::]:443:443"` as second entries, parameterised like the v4 ones so the rig can use
  `[::1]`), or drop the AAAA advice. Confirm at G1 with `ss -lnt` (`*:443` and `[::]:443`) and add `ABB_PUBLIC_ORIGIN` IPv6 to
  `--server-probe`. Until then this is `UNVERIFIED (env)` either way; the guide should say which it is.

### P2-3 A stale backup lock stops every later nightly backup until a human notices

- `scripts/hosting-backup.sh:92-93` (`mkdir "$LOCKDIR" || die ... 75`), `:62` (removed only by the EXIT trap), `deploy/hosting/systemd/
  abb-backup.service:19` (`TimeoutStartSec=3h`), `scripts/hosting-bootstrap.sh:235` (automatic reboot at 04:30).
- A power loss, an OOM of the shell, a `SIGKILL` from the 3 h timeout, or the 04:30 security reboot landing inside a long backup (02:30
  + up to 10 min delay + a slow B2 upload) leaves `/opt/abb/backups/.lock`. Every following run exits 75 at once. The age alert says
  something 36 h later; `AbbHostingBackupRunFailed` reads `last_run_success`, which the cleanup trap only writes when the trap runs, so a
  killed run records nothing. The runbook (`hosting-operations.md:66`) knows and tells the operator to remove it by hand.
- Fix: `flock -n` on a lock file (the kernel releases it when the process dies, whatever the reason), or write the pid into the lock
  directory and treat a dead pid as stale. Same for `scripts/hosting-upgrade.sh:72-73` (operator-run, lower stakes). Consider moving the
  reboot window after the backup window (`Automatic-Reboot-Time "05:30"`) or `Before=`-ordering; `flock` alone fixes the outcome.

### P2-4 Failed `create` steps leave orphans in `backups/.staging/` that nothing prunes

- `scripts/hosting-backup.sh:102-104` (staging under `/backups/.staging/$NAME`, uid 10001, mode 0700), `:76` (the failure path removes
  `$BACKUP_DIR/$NAME` only, i.e. the post-`mv` location), `:125-135` (`prune_local` lists top-level `abb-*` only).
- A `backup create` that fails after writing part of its bundle (disk full, OOM at `mem_limit`, a dump error) leaves `.staging/abb-<ts>`
  owned by uid 10001; the next run uses a new name and removes only its own staging path. A backup that fails every night grows the disk
  one partial dump at a time until `HostDiskUsageHigh`; the deploy user cannot even `rm` the directories without the root container.
- Fix: in `cleanup`, `root_sh "rm -rf /backups/.staging/$NAME"` when `local_ok != 1`; at the start of a run remove `.staging/*` older
  than a day (same `root_sh`, fixed arguments). Add a rig case: a planted failure in `create` leaves no `.staging` entry.

### P2-5 The OOM-kill alert is probably a false negative on a real host

- `scripts/hosting_backup_lib.py:203` reports `State.OOMKilled` from `docker inspect`; `deploy/hosting/rules/host.yml:118-128`
  (`AbbContainerOOMKilled`, page); the sampler runs once a minute (`abb-container-metrics.timer`).
- Docker resets `State.OOMKilled` when the container starts again, and `restart: unless-stopped` restarts it within seconds, so a
  one-a-minute `docker inspect` of a running container almost always sees `false`. The unit tests feed hand-written inspect JSON, and no
  rig produced a real OOM (the plan's step 9 evidence lists none), so the page alert that the overlay's `mem_limit` design relies on is
  unproven. `AbbContainerRestarting` (3 restarts in 30 min) is the backstop; a container killed once a day never trips it.
- Fix: prove it in `hosting-obs-e2e.sh` with a throwaway container (`mem_limit: 8m`, a loop that allocates) and see which flag survives
  the restart; if it does not, report `State.ExitCode == 137` of the last exit alongside, or consume `docker events --filter event=oom
  --since <last run>` in `hosting-container-metrics.sh`. Until proven, mark the OOM alert `UNVERIFIED (env)` in the plan and the runbook.

### P2-6 Guide step 10 runs `--server` without root: a false FAIL and silent SKIPs

- `docs/hosting-guide.md:257` (`cd /opt/abb && scripts/hosting-verify.sh https://... --server`, as `deploy`);
  `scripts/hosting_server_checks.py:91-96` (non-zero means SKIP), `:332-338` (`fail2ban-client` exits 255 for "not root" and the check
  reports FAIL, not SKIP).
- Followed literally: `ufw status`, `sshd -T` and `timedatectl` are SKIP (not PASS, as intended), and `fail2ban.sshd` is a red FAIL on a
  correctly configured host; the exit status is 1 and A12's "all PASS" can never be produced from that command.
- Fix: `sudo scripts/hosting-verify.sh ... --server` in the guide and runbook (the script's own docstring already says "as root");
  return SKIP for fail2ban when not root (uid check) so the verdicts stay honest.

## P3 (optional)

- **P3-1** Receiver name mismatch: `docs/runbooks/hosting-operations.md:82` and `deploy/hosting/rules/host.yml:281` say alerts go to
  the `disabled` receiver; the config (`alertmanager.yml.tmpl:46,66`) names `notify` and `deadman`.
- **P3-2** `scripts/hosting-secrets.sh:89-91` prints `KEPT .../secrets/metrics_token` right after the NOTE that the file does not
  exist; print KEPT only when it exists.
- **P3-3** `scripts/hosting-upgrade.sh:84,88`: with an incomplete `.env` (OIDC secret still empty, the state after step 6 until the
  owner edits it) `dc config` fails and `pg_image()` dies with a Python traceback instead of naming the missing variable; run
  `dc config -q` first and surface Compose's own `set ABB_OIDC_CLIENT_SECRET` message.
- **P3-4** `scripts/hosting-backup.sh:86` refuses `/...` and `.…` remotes but not a bare `bucket/abb` (no colon), which rclone treats
  as a local path; require `*:*`.
- **P3-5** `scripts/hosting_verify_lib.py:492-527`: the closed-path assertion could also send normalisation variants (`/METRICS`,
  `/metrics/`, `/%6detrics`, `/v1/../metrics`, `//metrics`). Caddy 2.11 matches the cleaned and unescaped path and the API only receives
  `/v1/*` and `/healthz` prefixes, so no bypass exists today (and the web relay, `upstream.ts:33-77`, is an allowlist without
  `/metrics`), but the check is cheap and guards a Caddy change.
- **P3-6** SSH: `MaxAuthTries 4` (`hosting-bootstrap.sh:257`) with an agent holding several keys produces "Too many authentication
  failures" that looks like a lockout; mention `IdentitiesOnly yes`/`-i` in the guide's step 5.
- **P3-7** `AbbHostingBackupNeverRan` (`host.yml:191-194`) restarts its 36 h clock on every Prometheus restart (04:30 security reboots,
  overlay upgrades); it still fires eventually. Note it in the rule comment.
- **P3-8** The restore drill container inherits the `backup` service's RW mount of the production `artifacts` volume
  (`docker-compose.yml:158`); the tool writes only to `--artifact-dir /scratch/artifacts`, but a tool bug could reach production
  artifacts. A `:ro` override is not possible with `run -v` on a named volume; note it in the drill's header as a trust boundary.
- **P3-9** `scripts/hosting-bootstrap.sh:126-132`: a later change to `daemon.json` (re-run after editing `ABB_*`) restarts Docker and
  kills running containers if `live-restore` was not already active; say so in the step description.
- **P3-10** `hosting-verify.sh https://abb.<domain>` from the owner's machine spends that address's failed-auth (30 bad keys) and
  sign-in-start budgets on purpose; the guide's step 10 could say that a throttled minute afterwards is expected, or suggest
  `--no-flood` for routine runs (the upgrade script already uses it).
- **P3-11** Caddy's `respond 404`/`413` answers carry no HSTS (the apps own it); harmless because `/` sets it, but `headers.hsts_once`
  only looks at `/` and `/v1/...`; note in the template.

## Areas checked with no substantive finding

- **Caddy front door** (`abb.caddy.template`): closed paths listed before the API route (first matching `handle` wins), `Content-Length`
  guard plus `max_size` for chunked bodies (the e2e found and fixed the read-triggered `max_size` gap), `encode` on an explicit
  content-type list that excludes `text/event-stream`, `flush_interval -1`, `-Server`/`-Via` only (deletions are deferred so they
  apply to proxied responses), no write timeout for streams, access log with headers and query strings stripped, HTTP/3 off, no
  `trusted_proxies` (client-supplied `X-Forwarded-For` is replaced). Cap drop + `NET_BIND_SERVICE`, `no-new-privileges`.
- **Public surface**: production overlay renders no `ports:` on any application service (verified with `config --format json` here);
  Prometheus/Alertmanager/Grafana bind `127.0.0.1`; the Caddy container is the only publisher; `--server` checks `docker ps`, `ss`, ufw
  and the Hetzner firewall is documented as the second layer. The web relay is a strict route allowlist, so `/api/abb/metrics` does not
  exist. The `--server-probe` private-port list covers 5432/5433/3000/3001/8000/8900/9090/9093/9100/2019/3100.
- **Trusted-proxy handling**: `client_address()` returns `None` with trust off (no per-address budget at all, hence the verifier's
  negative control goes red) and the LAST `X-Forwarded-For` entry with trust on; Caddy replaces the header; the forged-header check
  rotates both key ids and addresses so only a shared real address can trip the budget. U2 (the relay drops `X-Forwarded-*`) is
  honestly recorded as KI-311.
- **Secrets**: `umask 077`, `set -C` exclusive creates, 0600/0700, values never on a command line or in output (`openssl rand` piped
  into builtins), the passphrase travels by environment into the container (`-e NAME`, value not on the CLI), never regenerated;
  `.env` is required-variable interpolated (`:?`) so a missing secret stops Compose instead of falling back to a dev default;
  `hosting-verify.sh` reads token files and never prints them; Prometheus takes the token from a file; Grafana from `__FILE`; the
  Alertmanager examples use `*_file` fields; the log formats strip headers.
- **Key generation entropy**: `openssl rand -hex 24/32`, `-base64 33` (192-264 bits).
- **OIDC**: issuer defaults to `https://accounts.google.com`, `ABB_OIDC_EXTRA_HOSTS` carries the two Google hosts, the redirect URI
  in the guide matches `ABB_WEB_ORIGIN + /api/auth/callback`, `ABB_ALLOW_DEV_SESSIONS=0`, `fake-oidc` moved to a never-started profile
  (verified absent in the rendered config), invitation-only behaviour is the product's (ADR-157) and the guide says so.
- **SSH hardening**: two-stage, refuses without a logged publickey login AND a live session AND working sudo AND an `authorized_keys`,
  `sshd -t` then `sshd -T` effective check (catches a conflicting drop-in), reload not restart, listener re-check with rollback, `00-`
  ordering beats cloud-init's `50-`, ufw `limit` on every sshd port plus the current `SSH_CONNECTION` port before `enable`, root never
  loses its current session. Idempotent `step` runner with end-state tests.
- **Backup encryption and the drill**: the existing tool encrypts; the drill verifies before touching any database, validates the
  drill database name, refuses `abb`, refuses an existing database, creates and drops only what it created (`db_created` guard),
  table names from the manifest are regex-validated before they reach SQL, bundle names must match `abb-<UTC>` so a hostile remote
  listing cannot inject paths, `COMPLETE` is written last and required on download. The production database is only read (dump) and
  never written.
- **Prune logic** (`hosting_backup_lib.py`): newest-N protection, require-anchor (a wrong or empty listing deletes nothing), foreign
  names ignored, future-dated kept, incomplete bundles get a grace period; `rclone purge` only on validated names, never on an empty
  string (`for old in $victims` iterates zero times).
- **Shell hygiene**: `set -euo pipefail` everywhere, `${BACKUP_DIR:?}` guards on every `rm -rf`, pipelines captured before `grep -q`
  (the SIGPIPE/pipefail trap was found and fixed), `</dev/null` on `docker compose exec` inside the read loop, unquoted expansions only
  where word-splitting is intended and commented. The e2e scripts use their own project names (`abb-hosting-*`) and ports
  18080/18443/28443; their `down -v` targets those projects only.
- **systemd sandboxing**: `ProtectSystem=strict` with the two needed `ReadWritePaths`, `PrivateTmp`, `NoNewPrivileges`; connecting
  to `/run/docker.sock` on a read-only mount works (the EROFS check applies to regular files, directories and symlinks, not sockets);
  the textfile is written with `mkstemp` + `rename`, mode 0644, in a 0755 directory, readable by node_exporter's `nobody`. The timer
  is `Persistent=true`. Marked `UNVERIFIED (env)` in the plan, correctly.
- **Alert rules**: metric names match their producers (`node_filesystem_*`, `node_memory_*`, `probe_success`,
  `probe_ssl_earliest_cert_expiry`, the `abb_hosting_*` names in `hosting_backup_lib.py`, `process_start_time_seconds{job="prometheus"}`
  from the self-scrape, `up{job=...}` for the jobs defined in `prometheus.yml`); the application rules' `job=~"abb-.*"` matches
  `abb-api`/`abb-worker`. `absent() and on()` is well-formed. Every alert has a firing and a silent promtool case plus a mutation run
  (plan evidence). Severity labels match the Alertmanager routes and inhibitions.
- **Docs honesty**: the guide opens with an explicit VERIFIED / `UNVERIFIED (env)` split, every owner step is `ACTION NEEDED`,
  prices carry "confirm", the Google steps and redirect URI are correct, `docker volume prune` / `down -v` warnings are loud, and
  `docs-commands-check` validates the commands and variable names. The exceptions are P1-3, P1-4 and P2-6 above.
- **Memory arithmetic**, PostgreSQL 17 pin (ADR-178 Amendment 1), Dependabot directories and the `check-pins` coverage of the new
  tree are as described in the plan.

## Disposition (fixer pass, 2026-10-19)

Each fix was committed on its own. "Before" is the failing run or test observed before the fix; all rigs below were run to green at the end.

| Finding | Disposition | Evidence |
| --- | --- | --- |
| P1-1 web image not rolled back | fixed in 0c6a81d | `hosting-upgrade-e2e` U3 now asserts the web build and image id: before, `the web image was not rolled back (running build is 3, expected 2)`; after, PASS. The overlay names `${ABB_WEB_IMAGE:-agent-black-box-web:local}`. |
| P1-2 stopped PostgreSQL read as first deploy | fixed in ef19d6d | New rig case U2b (stack stopped): before, `treated as a first deploy`; after, backup taken and bundle at the remote. A failed query is fatal; first deploy is decided from the missing `pgdata` volume or an unmigrated database. The "PostgreSQL will not start" branch is `ASSUMED` (no rig plants a broken volume). |
| P1-3 monitoring overlay dropped on upgrade | fixed in 65b8460 | `scripts/hosting-compose.sh` is the single source (`ABB_OBS_UID` in `.env` turns the overlay and profile on; overrides documented in `backup.env.example`, guide step 9, runbook). Unit tests; rig U2 asserts the worker keeps `ABB_WORKER_METRICS_PORT`: before (old scripts), `the upgrade removed the worker's metrics port`. |
| P1-4 restore runbook not executable | fixed in cf6b09e | Procedure rewritten (start postgres, drill, restore into the empty `abb`, `hosting-upgrade.sh`). `hosting-backup-e2e` A7.9 runs the same commands on wiped volumes: 20 tables equal the manifest, artifact owned by uid 10001, `/readyz` answers. A real replacement server stays `UNVERIFIED (env)` (KI-312, text updated). |
| P1-5 KNOWN_ISSUES regression | fixed in 935c21a (the review commit; verified: filed `#NNN` links present) | `grep` of the register. |
| P2-1 shared `edge` network | accepted + documented, filed as KI-316 (S3, to be filed) | ADR-178 D10, guide second-app section, `docs/KNOWN_ISSUES.md`, `phase-23-ki-issues.md`. Per-app networks are a design change and not implemented, as instructed. fdbaec5 |
| P2-2 AAAA with an IPv4-only edge | fixed in fdbaec5 | AAAA instruction removed; guide says IPv4 only and how IPv6 would be added. |
| P2-3 stale backup lock | fixed in baae359 | pid-in-lock with stale takeover (portable; no flock on macOS) for backup and upgrade; unit tests fail without the helper. A lock without a pid file still counts as held. |
| P2-4 staging orphans | fixed in 3f9237d | Rig A7.7b: before, `staging holds [abb-20200101T000000Z abb-fresh ]`; after, orphan older than a day swept, fresh entry kept; a failed run removes its own staging path (that branch is `ASSUMED`; no rig plants a failing `create`). |
| P2-5 OOM alert false negative | fixed in 0fbe9ae | Measured on Docker: after an automatic restart `State.OOMKilled=false` (2 restarts, 3 oom events). The sampler now counts `docker events` oom events (`abb_hosting_container_oom_events`), the alert fires on either signal. `hosting-obs-e2e` O2b kills a real 16 MiB container: before, `the sampler did not report the OOM kills from the event log`; after PASS. promtool case and unit test added; mutation run green. The real page delivery on a server remains `UNVERIFIED (env)`. |
| P2-6 `--server` without root | fixed in 3e708cf + fdbaec5 | fail2ban without root is SKIP (unit test failed before); guide and runbook use `sudo`. |
| P3-1 receiver names | fixed in 06968c6 | |
| P3-2 KEPT message | fixed in 06968c6 | |
| P3-3 traceback on incomplete `.env` | fixed in ef19d6d | `dc config -q` first; Compose's own message is shown. `ASSUMED` (not exercised in a rig). |
| P3-4 colon-less remote | fixed in 06968c6 | Rig case in A7.4 (`bucket/abb` refused). |
| P3-5 closed-path normalisation variants | accepted | No bypass today (Caddy matches the cleaned path; the API only receives `/v1/*` and `/healthz`); revisit when the Caddyfile changes. |
| P3-6 `IdentitiesOnly` hint | accepted | Documentation nicety; no safety impact. |
| P3-7 `AbbHostingBackupNeverRan` clock reset | accepted | It still fires eventually; noted here. |
| P3-8 drill container RW mount of `artifacts` | accepted | Trust boundary: the tool writes only to its scratch directory; a `:ro` override is not possible with `run -v`. |
| P3-9 `daemon.json` change restarts Docker | accepted | Bootstrap re-runs after editing `ABB_*` are rare and the guide marks the step; no change. |
| P3-10 verify spends the address's budgets | accepted | Intended; `--no-flood` exists and the upgrade script uses it. |
| P3-11 HSTS on Caddy's own 404/413 | accepted | Harmless; `/` carries HSTS. |
