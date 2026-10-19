# Runbook: operating a hosted Agent BlackBox (one server)

For the single-server recipe of Phase 23 (`deploy/hosting/`, ADR-178). Everything is run on the server as the `deploy` user in `/opt/abb`, with the compose files
`docker-compose.yml deploy/hosting/compose.production.yml` (add `deploy/hosting/compose.observability.yml` and `--profile observability` for the dashboards). The alerts in
`deploy/hosting/rules/host.yml` link to the sections below by heading; the application alerts are in the other runbooks (`README.md`). The upgrade, rollback, resize and
second-app procedures are added in the hosting guide step; this page holds the alert responses.

Never run `docker volume prune` or `docker compose down -v` on the server: both delete the database volume. `docker image prune` (dangling images only) is safe.

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

1. `docker ps -a --format 'table {{.Names}}\t{{.Status}}'` and `docker inspect -f '{{.RestartCount}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' <container>`.
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
3. **Failed**: the line `hosting-backup: ERROR:` names the step. Common: the passphrase file is not mode 0600, `rclone` cannot reach the second provider (key, bucket or network: `rclone lsd <remote>:`), or a previous run left `/opt/abb/backups/.lock` after a crash (remove it if no backup is running).
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

Alertmanager ships with the `disabled` receiver: alerts are evaluated and shown in Prometheus and Alertmanager, and sent nowhere, until a receiver is configured (`deploy/hosting/alertmanager/alertmanager.yml.tmpl`, the commented examples; the choice of email, Slack or a webhook is the owner's).

1. Is the pipeline alive: the always-firing `Watchdog` alert should reach the dead-man's switch URL (`ABB_DEADMAN_URL` style service). If that service reports silence, check Prometheus and Alertmanager (`docker compose ... ps`), then the receiver's own credentials.
2. Alertmanager's view over an SSH tunnel: `ssh -L 9093:127.0.0.1:9093 deploy@<server>`, then `http://127.0.0.1:9093`. The same for Prometheus (9090) and Grafana (3001): none of them is reachable from the internet.
3. `docker logs <alertmanager container>` shows notification errors with the receiver name (never the secret).
