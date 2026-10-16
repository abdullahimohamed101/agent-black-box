# Phase 19b step 6: the performed restore drill (informational numbers, RPO and RTO)

Measured 2026-10-16 with `scripts/backup-restore-e2e.sh` (`make backup-restore-e2e`), twice, each run ending `BACKUP-RESTORE-E2E PASSED` (45 checks, 0 failed): the default dataset and a larger one
(`BACKUP_RESTORE_E2E_RUNS=1500 BACKUP_RESTORE_E2E_EVENTS=40`). A **regression baseline for one laptop, not a capacity claim**. **The durations and sizes are recorded, never asserted**: CI asserts the facts below
(equal digests, byte-identical files, exit codes), not how long they took.

## What the drill proves (all asserted)

Seed through the real API (runs, events, 40 KB artifacts of known bytes, an alert destination with a sealed signing secret and a TLS receiver, a retention policy, quota overrides, a session) ->
back up three ways -> **destroy the source (drop the database, delete the artifact directory)** -> restore into a fresh database and directory -> start the API and worker on it.

| Claim | Observed |
| --- | --- |
| Backup with the module CLI verifies; bundle is `0700`/`0600`; the manifest records the schema revision | `result=ok`, revision `0104`, modes `0700`/`0600` |
| Backup through the **compose `backup` service built from the API image** (`postgresql-client`, `openssl`, uid 10001 writing the mounted directory) | image built, `backup create` and `backup verify` exit 0 inside the container, row counts in its manifest equal the module CLI's (KI-210 resolved; what it found is KI-218) |
| Encrypted bundle | ciphertext (no `PGDMP` magic); right passphrase restores to the same content; a wrong passphrase exits 4 and **restores nothing** (no table, empty artifact directory) |
| Refusals through the CLI | tampered `artifacts.tar`: verify and restore exit 4 naming the file; non-empty database and non-empty artifact directory: exit 3, untouched |
| Restored database | 50 tables: every row count and **content digest** equal; every artifact file **byte-identical** (sha256); grants identical (114 table, sequence and column privilege entries); alembic head equals the code head |
| The API and worker on the restored data | the same runs, retention policy, quota overrides and destinations (content digest equal); the owner's session from before the backup still works; a restored run's events are served; new runs are ingested |
| The sealed alert secret opens | a test alert reaches the TLS receiver and its HMAC verifies with the **original** signing secret (the key came back with `server_secrets`) |
| An **older bundle** (taken on a database at the previous migration revision, `0103`) | restored with `upgraded=true`, `revision_in_bundle=0103`, `revision_now=0104`; the older bundle's data is there |
| Rotation on the restored installation | `rotate alerts --new-key` re-seals every destination (`failed=0`), the ciphertext and key id change, after a restart the destination still delivers and verifies, a rerun says `verdict=old_keys_can_be_retired` |

## Environment

| | |
| --- | --- |
| Host | Apple M5 Pro (15 cores), macOS (Darwin 25.6), the developer machine, **shared with other running work**: 1-minute load average 2.2 to 2.5 before the runs, 2.2 to 2.5 after |
| Database | PostgreSQL 16 (the Compose container in the Colima VM, host port 5433); `pg_dump`/`pg_restore` 16 on the host, the client in the image is PostgreSQL 17 |
| Disk | the machine's SSD; the bundle, the source and the restored copy are on the same volume |

## Numbers

| | default dataset | larger dataset |
| --- | --: | --: |
| Dataset | 120 runs, 1,440 events, 240 artifacts, 9.6 MB of artifacts | 1,500 runs, 60,000 events, 3,000 artifacts, 120 MB of artifacts |
| Bundle size (plain) | 10.5 MB | 131.9 MB |
| Bundle size (encrypted) | 10.5 MB | 131.9 MB |
| Backup, module CLI (plain) | 0.71 s | 1.22 s |
| Backup, module CLI (encrypted) | 1.03 s | 1.74 s |
| Backup, compose service (image already built; `docker compose run`) | 1.4 s | 4.7 s |
| Image build (cached layers) | 1.7 s | 1.9 s |
| **Restore, plain (wall, whole command)** | **1.22 s** (tool: 0.91 s) | **2.47 s** (tool: 2.15 s) |
| Restore, encrypted (wall) | 1.95 s | 3.68 s |

The compose build was cached; a cold build downloads the base image and the client packages and takes minutes (CI pays that each run).

## RPO and RTO, from what was observed

* **RPO = the interval between successful backups.** The backup holds one database snapshot (data written after it is not in it), so a schedule of `N` minutes loses up to `N` minutes of writes plus the time of the backup itself. There is no
  point-in-time recovery; WAL archiving is the documented upgrade path when a smaller RPO is needed and is not built (KI-203).
* **RTO = the restore time plus the time to create the empty database, restore the roles and passwords you keep outside the bundle, point the processes at it and start them.** The restore command itself took
  **1.2 s for 10 MB and 2.5 s for 132 MB** here, and grew with the data (the database load and the tar unpack dominate; the verification of counts and files is part of that time). That is **indicative, not asserted, and a lower bound for a real
  installation**: it is a local database, nothing else was writing, the bundle was on local disk (not fetched from remote storage), and the datasets are two to three orders of magnitude below a production database (KI-219). Re-run the drill
  on a copy of your own data and quote that figure.

The restore time scales roughly with bundle size; if your bundle is 100 times this one, plan for minutes, not seconds, and measure.
