# Backup and restore

Tool: `python -m abb_api.backup create|verify|restore` (ADR-162). Run it as an operator with the database **owner** role; it is never part of the API process. A bundle holds the whole database including `server_secrets` (the alert and event-id keys) and the artifact files: **it is as sensitive as the database**. Encrypt it (`--encrypt`) or keep it on encrypted storage, and never leave it world- or group-readable (the tool creates it `0700`/`0600`).

## What a bundle is

A directory: `MANIFEST.json` (readable; schema revision, row counts of 20 key tables, SHA-256 of every file and every artifact, tool and PostgreSQL versions, nothing secret), `database.dump` (`pg_dump -Fc` taken in one snapshot) and `artifacts.tar` (copied *after* the dump, so no artifact row ever points at a missing file). Encrypted bundles carry `.enc` files and a `manifest_mac`.

## Make a backup

```sh
# native (reads .env; OUT must be a new or empty directory)
make backup OUT=/var/backups/abb/$(date +%F) BACKUP_FLAGS="--encrypt --passphrase-file /etc/abb/backup.pass"
# compose (profile "ops"; the volume ./backups is mounted at /backups, see below)
docker compose --profile ops run --rm backup create --out /backups/$(date +%F) --encrypt --passphrase-env ABB_BACKUP_PASSPHRASE
```

Environment: `ABB_BACKUP_DATABASE_URL` (else `ABB_MIGRATION_DATABASE_URL`, else `ABB_DATABASE_URL`; the owner role, because the dump reads every table), `ABB_ARTIFACT_DIR` or `--artifact-dir`. The passphrase is a file (mode 0600, `--passphrase-file`), the NAME of an environment variable (`--passphrase-env`) or a file descriptor (`--passphrase-fd`); at least 12 bytes; never on the command line. `pg_dump` must be at least as new as the server (the API image ships a current client; natively `ABB_PG_DUMP` can point at one).

Exit codes: 0 ok; 2 usage/configuration; 3 refused (output not empty, passphrase file readable by others, bundle from a newer schema, restore target not empty); 4 integrity (checksum mismatch, wrong passphrase, tampering; the message names the file); 5 a tool failed (pg_dump, pg_restore, openssl, alembic, database unreachable); 6 the restore finished but does not match the manifest; 7 the backup would be inconsistent (an artifact row has no file). On any failure `create` removes what it wrote.

**Ordering advice.** Run retention *before* the backup, not during it: an enforcing retention tick that deletes artifact files between the dump and the copy makes the backup fail with 7 (it never writes a bundle that lies). If it does, rerun. The backup holds one database snapshot for its duration (like a long `pg_dump`).

### Schedule it

systemd timer (preferred):

```ini
# /etc/systemd/system/abb-backup.service
[Service]
Type=oneshot
User=abb
EnvironmentFile=/etc/abb/backup.env          # ABB_BACKUP_DATABASE_URL, ABB_ARTIFACT_DIR (0600, owner abb)
ExecStart=/usr/local/bin/abb-backup.sh
# /etc/systemd/system/abb-backup.timer
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

`abb-backup.sh`: `set -e; D=/var/backups/abb/$(date +%F); python -m abb_api.backup create --out "$D" --encrypt --passphrase-file /etc/abb/backup.pass; python -m abb_api.backup verify "$D" --passphrase-file /etc/abb/backup.pass; find /var/backups/abb -maxdepth 1 -mtime +14 -exec rm -rf {} +`. Cron works the same (`30 2 * * * abb /usr/local/bin/abb-backup.sh`). Copy bundles off the machine (and test that the copy verifies).

**RPO = the interval between successful backups.** There is no point-in-time recovery here: for a smaller RPO use managed PostgreSQL with WAL archiving (documented upgrade path; not built).

## Check a bundle (no database touched)

```sh
make backup-verify BUNDLE=/var/backups/abb/2026-10-16 BACKUP_FLAGS="--passphrase-file /etc/abb/backup.pass"
```

Recomputes every checksum (and, encrypted, the manifest's HMAC). Do this after every copy. A plain bundle's manifest is not authenticated: verification proves the files match the manifest, not that nobody replaced both; an encrypted bundle proves it.

## Restore

A restore loads into a **new, empty database** and a **new or empty artifact directory**; it never drops, truncates or overwrites, and there is no flag to override that. Procedure:

1. Stop the API, worker and retention processes that point at the old data if this is a recovery in place (point them at the new database afterwards).
2. Create the empty database as the owner role (`CREATE DATABASE abb_restored OWNER abb;`). The roles `abb_runtime` and `abb_retention` must exist on the server (any database that ever ran the migrations created them; otherwise the tool creates them `NOLOGIN` if the role has `CREATEROLE`, or says exactly which to create).
3. `ABB_RESTORE_DATABASE_URL=postgresql+asyncpg://abb:...@host/abb_restored` (deliberately no fallback) and run:
   ```sh
   make restore BUNDLE=/var/backups/abb/2026-10-16 ARTIFACT_DIR=/data/artifacts-restored BACKUP_FLAGS="--passphrase-file /etc/abb/backup.pass"
   ```
   The tool: verifies the bundle; refuses a newer-schema bundle or a non-empty target; scans the artifact tar against the manifest; loads the database in **one transaction** (a failure leaves it empty); unpacks the artifacts; checks row counts, the schema revision and that every artifact row's file exists; then runs `alembic upgrade head` (upgrading an older bundle, and applying `ABB_RUNTIME_DB_PASSWORD` / `ABB_RETENTION_DB_PASSWORD` to the roles). It prints `result=ok revision_in_bundle=... revision_now=... upgraded=... artifact_files=N seconds=S`.
4. Point `ABB_DATABASE_URL`, `ABB_RETENTION_DATABASE_URL` and `ABB_ARTIFACT_DIR` at the restored database and directory and start the processes. The roles' logins come from the passwords given to `alembic upgrade` (the restore ran it), not from the bundle.
5. Check: sign in, open a recent run, open an artifact, send a test alert (`POST .../destinations/{id}/test`): the destination opens because `server_secrets` came with the dump.

If the tool exits 6, or any step after "database loaded" failed, **drop the restored database and remove the artifact directory contents** before retrying; the message says so. Exit 4 with a passphrase message leaves nothing created.

Restore time (RTO) depends on size; the tool prints `seconds=`. The measured drill on the Docker stack is recorded in `docs/benchmarks/` (Phase 19b step 6).

## Restore drill (what "performed" means)

Before enabling `ABB_RETENTION_MODE=enforce` perform a drill and keep its output: back up, restore into a scratch database and directory, open a run and an artifact, confirm the alert destination test works, drop the scratch copies. `tests/backup/` does this in CI with seeded data (counts and row content equal, artifacts byte-identical, an alert secret opens, the API serves the restored runs, grants identical).

## Not in the bundle

The operator's `.env` and secrets stored outside the database (OIDC client secret, SMTP password, role passwords, the backup passphrase), role logins, PostgreSQL configuration, anything on the artifact volume that is not a store key. Keep them in your secret manager; `docs/runbooks/secrets.md`.

## Compose notes

The `backup` service (profile `ops`) connects as the owner and mounts `./backups` (override with `ABB_BACKUP_DIR`) at `/backups`. The container runs as uid 10001: that directory must be writable by it (`chmod 0770` with a matching group, or `chown 10001`). Not yet exercised on the full stack (step 6).

## Monitoring

Dashboard row: **Retention and backups**. Alerts: `AbbBackupTooOld` (36 hours, ticket), `AbbBackupVeryOld` (72 hours, page), `AbbBackupNeverSucceeded`, `AbbBackupRunFailed`.

Backups are not scraped; `python -m abb_api.backup create --metrics-textfile PATH` (or `ABB_METRICS_TEXTFILE`) writes `backup_last_success_timestamp_seconds`, `backup_last_duration_seconds`,
`backup_bundle_bytes` and `backup_runs_total{outcome}` for node_exporter's textfile collector (OPERATIONS, "Backups, key rotation and retention"). A failed run does not erase the last success.

- Too old: the schedule did not run or runs fail. `systemctl status abb-backup.timer`, the unit log, free space and exit codes (above). Take a backup by hand, then verify it.
- Never succeeded: the textfile exists but records no success: usually a first run that failed; read the run's output.
- **A backup that is not scheduled at all writes no file and is not detected** (KI-243). Treat the first scheduled run as part of setup and check the series exists.
