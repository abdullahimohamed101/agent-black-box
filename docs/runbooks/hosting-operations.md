# Runbook: operating a hosted Agent BlackBox (one server)

For the single-server recipe of Phase 23 (`deploy/hosting/`, ADR-178); the set-up itself is the [hosting guide](../hosting-guide.md). Everything is run on the server as the `deploy` user in `/opt/abb`, with the compose files
`docker-compose.yml deploy/hosting/compose.production.yml` (add `deploy/hosting/compose.observability.yml` and `--profile observability` for the dashboards). The alerts in
`deploy/hosting/rules/host.yml` link to the sections below by heading; the application alerts are in the other runbooks (`README.md`). Contents: [routine care](#routine-care), the alert responses
(disk, memory, restarts, metrics, backups, certificate, alert delivery), [upgrade and rollback](#upgrade-and-rollback), [restoring onto a new server](#restoring-onto-a-new-server), [rotating secrets](#rotating-secrets), [growing the server](#growing-the-server)
and [incident basics](#incident-basics). Anything in this page that needs a real server is `UNVERIFIED (env)` until the owner has run it; the scripts it names were exercised in local rigs (the guide says which).

In this page `$dc` stands for `docker compose -f docker-compose.yml -f deploy/hosting/compose.production.yml --profile app --profile ops` run from `/opt/abb`. **Once the monitoring overlay is on (guide step 9), `$dc` must also carry `-f deploy/hosting/compose.observability.yml --profile observability`**: recreating `worker` without it drops its metrics port and `AbbTargetDown` pages. The scripts (`hosting-upgrade.sh`, `hosting-backup.sh`, `hosting-restore-drill.sh`) do this themselves: they read `scripts/hosting-compose.sh`, which adds the overlay when `.env` defines `ABB_OBS_UID` (step 9 writes it). Override with `ABB_OBSERVABILITY=0|1` or an explicit `ABB_COMPOSE_FILES` in `/etc/abb/backup.env`.

Never run `docker volume prune` or `docker compose down -v` on the server: both delete the database volume. `docker image prune` (dangling images only) is safe.

## Routine care

Weekly, five minutes, from your machine and the server:

1. `scripts/hosting-verify.sh https://abb.<domain>` from your machine: certificate, headers, closed paths. `sudo scripts/hosting-verify.sh https://abb.<domain> --server` on the server (root is needed for ufw, sshd and fail2ban; without it those are SKIP): firewall, ssh, published ports, secrets modes, backup freshness, disk.
2. `scripts/hosting-backup.sh --check` prints whether the last complete backup and its off-site copy are fresh (36 hours).
3. `df -h /` and `docker system df`. Remove dangling images after upgrades with `docker image prune` (dangling only). **Never `docker volume prune`, never `docker compose down -v`**: both delete the database volume.
4. `sudo journalctl -u unattended-upgrades --since "7 days ago" | tail`, and whether `/var/run/reboot-required` exists (`ls /var/run/reboot-required`). A reboot is safe: every service has `restart: unless-stopped`. Automatic reboots happen at 04:30 only when an update requires one, unless the bootstrap was run with `ABB_AUTO_REBOOT=0`.
5. The sign-in and invitation state of the product itself (members, keys): [sign-in runbook](auth-and-access.md).

Monthly: the restore drill, `scripts/hosting-restore-drill.sh --latest` (run as `deploy` from `/opt/abb`; it reads `/etc/abb/backup.env` and defaults `RCLONE_CONFIG` to `/etc/abb/rclone.conf` when that file exists, so no exports are needed outside systemd), which must print `DRILL OK`. Before enabling `ABB_RETENTION_MODE=enforce` do it first and keep the output.
Quarterly: read what Dependabot proposed for the pinned images and run `scripts/check-pins.sh` ([bumping pins](bumping-pins.md)); a PostgreSQL major version is never an in-place change.

## Disk is filling up

Alerts: `HostDiskUsageHigh` (80 percent), `HostDiskUsageCritical` (90 percent), `HostInodesLow`, `HostDiskFillingFast`.

1. Which disk and what is on it: `df -h /`, then `docker system df` (images, containers, local volumes, build cache) and `sudo du -xh --max-depth=2 /var/lib/docker /opt | sort -h | tail -20`.
2. Usual causes, in order: old images after upgrades (`docker image ls`; remove dangling ones with `docker image prune`), local backup bundles (`/opt/abb/backups`, kept 7 days by `ABB_LOCAL_KEEP_DAYS`), container logs (rotated at 3 x 10 MB each by `daemon.json`; a container created before the rotation was configured is not: recreate it), the PostgreSQL data itself (growth is data: check retention in the product, `ABB_RETENTION_MODE`), the artifact volume.
3. At 90 percent stop adding data until there is room: PostgreSQL stops accepting writes when the disk is full. Remove old backup bundles by hand if the list is long, never the newest ones, then `docker image prune`.
4. If the data is simply growing: resize the server (Hetzner "CPU and RAM only" keeps the disk and is reversible; a disk upgrade is not) or add a volume. Both are in the hosting guide.

## Memory is low

Alerts: `HostMemoryLow`, `HostSwapHeavilyUsed`.

1. `free -h` and `docker stats --no-stream`. Each ABB container has a `mem_limit` ceiling (`deploy/hosting/compose.production.yml`); the second application on the server has its own.
2. A build or a backup can spike memory for minutes; `for: 10m` skips those. A sustained shortage means the pair of applications no longer fits: resize to 8 GB (the hosting guide), or reduce what runs (stop the observability profile's Grafana when not in use).
3. If a container was killed, see the next section; the kernel's view is `sudo journalctl -k | grep -i oom`.

## A container keeps restarting

Alerts: `AbbContainerRestarting`, `AbbContainerOOMKilled`, `AbbContainerNotRunning`.

1. `docker ps -a --format 'table {{.Names}}\t{{.Status}}'` and `docker inspect -f '{{.RestartCount}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' <container>`. `oom=false` proves nothing after an automatic restart (Docker resets it); the proof is `docker events --since 24h --until "$(date +%s)" --filter event=oom`, which is also what the alert reads.
2. `oom=true`: the container hit its `mem_limit` (or the host ran out). Raise that one limit in the overlay if the host has room, otherwise resize the server. Docker clears the flag when the container is recreated (`docker compose up -d <service>`).
3. Exit code and the last log lines: `docker logs --tail 50 <container>`. Structured logs never hold payload bodies or secrets. A missing or wrong environment variable stops the API at start with a message naming it.
4. After an upgrade: see `scripts/hosting-upgrade.sh`'s output; it rolls back by itself when the schema did not change.

## The host metrics are stale

Alerts: `AbbHostMetricsStale`, `AbbHostScrapeDown`.

1. `systemctl status abb-container-metrics.timer abb-container-metrics.service`; the timer writes `/var/lib/node_exporter/textfile/abb_containers.prom` every minute. Enable it with `sudo systemctl enable --now abb-container-metrics.timer` if the bootstrap left it off.
2. `docker compose -f docker-compose.yml -f deploy/hosting/compose.production.yml -f deploy/hosting/compose.observability.yml --profile observability ps` shows which monitoring container is down; `docker logs` names why (a wrong file mode on a secret is the usual cause: the monitoring containers run as the deploy user).
3. While this alert is active the restart, out-of-memory and not-running alerts cannot fire.

## The backup is late or failing

Alerts: `AbbHostingBackupTooOld`, `AbbHostingBackupVeryOld`, `AbbHostingOffsiteTooOld`, `AbbHostingBackupNeverRan`, `AbbHostingBackupRunFailed`, `AbbHostingRestoreDrillOld`.

1. `systemctl list-timers abb-backup.timer`, `systemctl status abb-backup.service`, `journalctl -u abb-backup.service -n 100`. `scripts/hosting-backup.sh --check` prints the same judgement as the alerts.
2. **Never ran** (`AbbHostingBackupNeverRan`, KI-243): the timer is not enabled, or `/etc/abb/backup.env` is incomplete. `sudo systemctl enable --now abb-backup.timer`, run `sudo systemctl start abb-backup.service` once and read its output. The first scheduled run is part of the setup.
3. **Failed**: the line `hosting-backup: ERROR:` names the step. Common: the passphrase file is not mode 0600, `rclone` cannot reach the second provider (key, bucket or network: `rclone lsd <remote>:`), or **`Failed to load config file: permission denied`** (`/etc/abb/rclone.conf` was made with `sudo` and is root-owned: `sudo chown deploy:deploy /etc/abb/rclone.conf && sudo chmod 600 /etc/abb/rclone.conf`; `scripts/hosting-backup.sh --check` prints this cause), or a previous run left `/opt/abb/backups/.lock` after a crash (remove it if no backup is running).
4. **Off-site only**: the local bundle was made and verified but the copy failed; the local bundle is kept. Fix the remote and run the service again.
5. **Restore drill old**: `scripts/hosting-restore-drill.sh --latest` restores the newest off-site bundle into a throwaway database and prints `DRILL OK`. Do it monthly, and always before enabling `ABB_RETENTION_MODE=enforce`. The restore procedure itself is `backup-restore.md`.
6. Losing `/etc/abb/backup.pass` makes every bundle unreadable; it must also be in the owner's password manager.

## The site is down or the certificate is wrong

Alerts: `AbbPublicProbeFailing`, `AbbCertificateExpiring`, `AbbCertificateExpiringSoon`.

1. From your own machine: `scripts/hosting-verify.sh https://abb.<domain>` (the same assertions as the rig). From the server: `curl -sS -o /dev/null -w '%{http_code}\n' https://abb.<domain>/healthz`.
2. Is Caddy up: `docker compose -f /opt/edge/compose.yml ps` and `docker logs --tail 50 <caddy container>`. A Caddy configuration error keeps the previous configuration running when reloaded with `caddy reload`; a restart with a bad file does not start.
3. Certificate trouble: Caddy renews about 30 days before expiry. If it cannot, the cause is almost always DNS no longer pointing at this server, port 80 or 443 blocked (ufw, the Hetzner cloud firewall), or Let's Encrypt rate limits after repeated failures. The Caddy log lines name the failing challenge.
4. The probe runs from the server itself, so a failure from outside only (a Hetzner firewall rule, a DNS record) is found with `scripts/hosting-verify.sh --server-probe <server address>` from another machine.

## Alerts never arrive

Alertmanager ships with the `notify` receiver empty (and a `deadman` receiver for the watchdog): alerts are evaluated and shown in Prometheus and Alertmanager, and sent nowhere, until a receiver is configured (`deploy/hosting/alertmanager/alertmanager.yml.tmpl`, the commented examples; the choice of email, Slack or a webhook is the owner's).

1. Is the pipeline alive: the always-firing `Watchdog` alert should reach the dead-man's switch URL (`ABB_DEADMAN_URL` style service). If that service reports silence, check Prometheus and Alertmanager (`docker compose ... ps`), then the receiver's own credentials.
2. Alertmanager's view over an SSH tunnel: `ssh -L 9093:127.0.0.1:9093 deploy@<server>`, then `http://127.0.0.1:9093`. The same for Prometheus (9090) and Grafana (3001): none of them is reachable from the internet.
3. `docker logs <alertmanager container>` shows notification errors with the receiver name (never the secret).

## Upgrade and rollback

```sh
cd /opt/abb
scripts/hosting-upgrade.sh --dry-run <tag>      # the plan and the commits that would arrive; changes nothing
scripts/hosting-upgrade.sh <tag>
```

Order of the script (its comments are the reference): preflight (clean checkout, Compose 2.24 or newer, 5 GiB free) then a verified, off-site-copied backup (a failure stops everything and changes nothing), the running images tagged `:rollback`, checkout and build, migrations as the owner role before any new API process, `up --wait`, then `hosting-verify.sh`.

- **Failed before the schema changed:** the script restores the previous checkout and images itself and says `ROLLED BACK`. Nothing more to do; read the build or start error it printed.
- **Failed after the schema changed:** it says `NOT rolled back` and names the pre-upgrade bundle. An older release may not run on a newer schema. Either fix forward (a corrected release, then upgrade again) or restore the bundle: [restoring onto a new server](#restoring-onto-a-new-server) describes the steps, and [migrations](migrations.md) says which migrations can be reversed with `alembic downgrade` instead.
- **A PostgreSQL image change in the new release is refused** with the checkout put back. A major version needs a dump and restore: take a backup, plan the change as its own piece of work.
- `--skip-backup` exists for emergencies; do not use it after the first deploy.
- A held lock (`/opt/abb/.upgrade.lock`) means another upgrade is running or one died; remove the directory only after checking with `ps`.
- Rolling back by hand to the previous images is possible while the `:rollback` tags exist (`docker image ls | grep rollback`): `git checkout --detach <previous commit>` then `$dc up -d --no-build --wait api worker retention web`. Only do this when the schema revision is unchanged (`$dc exec postgres psql -U abb -d abb -tAc 'select version_num from alembic_version'`).

## Restoring onto a new server

For a lost or damaged server, or a failed upgrade that changed the schema. You need: the backup passphrase (your password manager), the rclone key for the second provider, the Google client secret, and the repository.

1. New server, DNS pointing at it, then the guide's steps 5 to 7 (bootstrap, code, `hosting-secrets.sh`, Caddy), **but not the first deploy**. **Put the saved passphrase back** into `/etc/abb/backup.pass` (mode 0600, owner `deploy`) instead of the newly generated one, and recreate `/etc/abb/rclone.conf` (as `deploy`, or `sudo chown deploy:deploy /etc/abb/rclone.conf` afterwards) and `ABB_OFFSITE_REMOTE` in `/etc/abb/backup.env`. Check out the release tag you were running (`git checkout --detach <tag>`).
2. Fetch the bundle and start only the database. A fresh volume creates an **empty** `abb`; nothing is migrated yet.

   ```bash
   cd /opt/abb && set -a && . /etc/abb/backup.env && set +a && export ABB_BACKUP_DIR=/opt/abb/backups
   rclone lsf -R --files-only "$ABB_OFFSITE_REMOTE" | python3 scripts/hosting_backup_lib.py latest-complete   # prints the newest complete bundle name, abb-<timestamp>
   NAME=abb-<timestamp>
   mkdir -p backups/incoming && rclone copy "$ABB_OFFSITE_REMOTE/$NAME" backups/incoming/$NAME --checksum && rm -f backups/incoming/$NAME/COMPLETE
   $dc up -d --wait postgres
   ```

3. Prove the bundle first, touching only a throwaway database: `scripts/hosting-restore-drill.sh backups/incoming/$NAME`. It must print `DRILL OK` with table counts equal to the manifest.
4. Restore straight into the empty `abb` and the real artifact volume with the tool ([backup and restore](backup-restore.md#restore)). It refuses a non-empty database, and creates the missing `abb_runtime` and `abb_retention` roles itself (the owner `abb` may). It runs as root in the container only so it can then hand the files to the API user:

   ```bash
   export ABB_BACKUP_PASSPHRASE="$(cat /etc/abb/backup.pass)"
   $dc run --rm -T --user 0:0 -e ABB_BACKUP_PASSPHRASE --entrypoint sh backup -c \
     'ABB_RESTORE_DATABASE_URL="$ABB_BACKUP_DATABASE_URL" python -m abb_api.backup restore /backups/incoming/"$0" --artifact-dir /data/artifacts --passphrase-env ABB_BACKUP_PASSPHRASE && chown -R 10001:10001 /data/artifacts' "$NAME"
   ```

   If it fails, nothing needs repairing by hand: `docker compose down` then remove only the data volumes of this project (`docker volume ls`, the `pgdata` and `artifacts` ones; this is the single case where deleting a volume is right, because the database is the empty one you just created), and repeat from step 2.
5. Start the application on the restored data with the upgrade script: `scripts/hosting-upgrade.sh <the tag> --no-verify`. The restored `alembic_version` makes it a normal deploy (it takes a backup first, runs the migrations, which set the role passwords and are a no-op when the revision is current, and starts `api worker retention web`). Then check `/readyz` from the server (`$dc exec api python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8000/readyz').status)"`), sign in, open a recent run and an artifact.
6. Run `sudo scripts/hosting-verify.sh https://abb.<domain> --server` and take a fresh backup straight away.

Evidence: steps 2 to 5 (download, drill, restore into the empty `abb`, migrate, API ready, row counts equal to the manifest, artifact readable by uid 10001) are exercised by `scripts/hosting-backup-e2e.sh` case A7.9 on wiped volumes. `UNVERIFIED (env)`: the same on a real replacement server (DNS, Caddy certificate, the real provider); KI-312 stays open until the owner has done it once.

## Rotating secrets

The full index is [secrets](secrets.md); the hosted specifics:

| Secret | Where on the server | Rotate |
| --- | --- | --- |
| Google client secret | `/opt/abb/.env` (`ABB_OIDC_CLIENT_SECRET`) | create a new secret in Google, edit `.env`, `$dc up -d api` (sign-ins in flight fail once), delete the old secret at Google |
| Metrics token | `.env` (`ABB_METRICS_TOKEN`) and `secrets/metrics_token` (the same value, both) | set a new value in both with an editor, `$dc up -d api worker`, restart Prometheus with the observability overlay |
| PostgreSQL role passwords | `.env` | PostgreSQL stored the first value when it created the volume; changing `.env` alone does not change the role. Follow [secrets](secrets.md) (`alembic upgrade head` applies the runtime and retention passwords; the owner password needs `ALTER ROLE`) |
| Backup passphrase | `/etc/abb/backup.pass` and your password manager | a new passphrase applies to new bundles only; keep the old one for as long as old bundles must stay restorable. There is no re-encryption tool |
| rclone key | `/etc/abb/rclone.conf` | create a new application key at the provider, `rclone --config /etc/abb/rclone.conf config` as `deploy` (not `sudo`) to update the remote, run `scripts/hosting-backup.sh` once, delete the old key |
| Alert receiver secrets | files under `secrets/alertmanager/` | replace the file (mode 0600, owner `deploy`), restart Alertmanager |
| Grafana admin password | `secrets/grafana_admin_password` | Grafana keeps its first password in its own database; change it in Grafana's UI as well as the file |
| Project API keys | the product | `create-key`, deploy it to the agent, `revoke-key` the old one |
| ssh keys | `/home/deploy/.ssh/authorized_keys` | add the new public key, test a login in a second terminal, then remove the old line |

Never put a secret on a command line, in the repository, or in a chat. Files that hold secrets must stay mode 0600 (`hosting-verify.sh --server` checks).

## Growing the server

Sustained `HostMemoryLow`, or a second heavy app: Hetzner console, **Rescale** to CX33 (4 vCPU, 8 GB) with **CPU and RAM only** so the disk is unchanged and the step is reversible; confirm the current price on Hetzner's page. The server restarts and the containers come back by themselves.
Afterwards: `hosting-verify.sh https://abb.<domain> --server`. If the disk is the limit, attach a Hetzner volume and move the Docker data root deliberately; do not grow the disk by rescaling unless you accept that it cannot be shrunk again. The disk and memory responses above come first.

## Incident basics

A compromised or misbehaving server, in this order: contain, preserve, rotate, restore.

1. **Contain.** If you suspect the host is compromised, take it off the network (Hetzner console: firewall with no inbound rules, or power off). If only a credential leaked, rotate it first ([above](#rotating-secrets)); for a leaked ingestion key, `revoke-key` is immediate.
2. **Preserve.** Before changing anything: `sudo journalctl --since "2 days ago" > ~/incident-journal.txt`, `docker ps -a`, `docker logs --tail 500 <container>` for each container, a Hetzner snapshot of the disk. Structured logs hold no event payloads or secrets, so they are safe to share with whoever helps you.
3. **Who signed in.** The product's audit log (Settings) and `$dc logs api | grep -i auth`; sessions can be ended in Settings, Sessions, or all at once ([secrets](secrets.md), sessions row).
4. **Rotate** everything in the table above that the host could read: all of it, if the host was compromised (anything in `/opt/abb/.env`, `/etc/abb`, the ssh keys, the Google client secret, the B2 key).
5. **Restore** onto a fresh server rather than cleaning the old one: [restoring onto a new server](#restoring-onto-a-new-server). The newest bundle may include whatever the intruder changed; pick one from before the earliest sign of trouble (`rclone --config /etc/abb/rclone.conf lsf <remote>`).
6. **Afterwards:** write down what happened and what would have caught it earlier. Application-level signals (unexpected keys, roles, approvals) are in [security operations](security-operations.md).

Things that look alarming and are not: a container restarted once after an automatic security update and reboot; Alertmanager showing only `Watchdog` firing (that is the healthy state); `HostDiskFillingFast` right after restoring a large bundle.
