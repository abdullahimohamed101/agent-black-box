# ADR-162: Backup bundles, restore rules, the migration safety table and alert-key rotation

Status: Accepted (Phase 19b, step 4). Implements D4 and D5 of the Phase 19b plan and gates 6 (the tool and its tests; the Docker-stack drill and recorded times are step 6) and 7 (migration table, rotation).
Builds on ADR-150 (sealed alert secrets, `server_secrets`), ADR-141 amendment 2 (the event-id key), ADR-159/160 (retention: enforcement is the reason a backup must exist and be proven).
Date: 2026-10-16
Phase: 19b

## Context

Enforced retention deletes permanently, secrets sit sealed under keys that live only in the database, and migrations have never said how to go back. An operator needs one command that captures the whole installation consistently, one that loads it into a new place and proves it matches, a plain statement of which migrations can be rolled back, and a rotation of the one key the product can rotate. Everything here is a command-line tool run by an operator with the database owner's credentials; the API process gains no new route and no new privilege.

## Decision

### 1. The bundle (`python -m abb_api.backup create`)

One directory, created mode `0700`, files `0600`, refused if it exists and is not empty (nothing is ever overwritten):

```
MANIFEST.json          readable JSON; never encrypted; nothing secret in it
database.dump[.enc]    pg_dump --format=custom of the whole database
artifacts.tar[.enc]    the artifact files (a plain tar; one member per store key)
```

`MANIFEST.json`: `format_version` (1), `created_at`, `tool` (name, version, the `pg_dump` banner), `postgres.server_version`, `schema_revision` (the alembic head at snapshot time), `tables` (row counts of a fixed list of 20 key tables, including `server_secrets`, `events`, `artifacts`, `audit_log`), `database` and `artifacts` entries (`file`, `size`, `sha256` of the stored bytes; for artifacts also `count`, `total_bytes` and `files`: key, size and sha256 of every artifact), `total_bytes`, `encryption` (null, or scheme and iteration counts and the MAC salt) and, when encrypted, `manifest_mac`. No connection string, host, user, password, key material or database name is recorded.

### 2. Consistency, and the ordering rule

- A `REPEATABLE READ, READ ONLY` transaction exports a snapshot (`pg_export_snapshot()`); the counts, the revision and the list of artifact rows are read inside it, and `pg_dump --snapshot=` dumps exactly that state. The manifest describes the dump, not the database a moment later. `pg_dump`'s own default is also a single snapshot; the explicit export is what lets the counts be taken in the same one. (`--serializable-deferrable` was rejected: it only matters for serializable workloads and can wait unboundedly for a safe snapshot.) The dump is streamed (`pg_dump` stdout to the file or to openssl), so a dump is never held in memory and, when encrypting, never exists unencrypted on disk.
- **The artifact files are copied after the dump.** The API writes an artifact's file before it inserts the row (`artifacts/service.py`; there is no foreign key from a row to a run or the reverse to a file), so every row visible to the snapshot has its file when the copy starts; files that arrive after the dump are extra and harmless. The one way a row can lose its file between dump and copy is a deletion (the retention job removes files first, then rows). So after the tar is written, every artifact row of the snapshot is checked against it; a missing file fails the backup with exit code 7 and removes what the run wrote. **A bundle therefore never contains a row pointing at a missing file.** The operator rule (OPERATIONS.md): back up outside an enforcing retention tick, and run retention *before* the backup so the bundle does not carry data about to expire.
- The snapshot is held for the duration of the dump and copy. It prevents cleanup of rows that die meanwhile (the same effect as a long `pg_dump`); bounded by the backup duration.

### 3. Encryption: `openssl enc` plus an HMAC

Optional, `--encrypt` with the passphrase from a file (mode 0600 at most, else refused), an environment variable **name**, or an inherited file descriptor, never an argument, never logged; minimum 12 bytes. Data is encrypted with `openssl enc -aes-256-cbc -pbkdf2 -md sha256 -iter 600000 -salt` (random salt in the file header; passphrase delivered through a pipe). `enc` is not authenticated, so the manifest carries `manifest_mac = HMAC-SHA256` over every other manifest field (which includes the SHA-256 of each ciphertext), keyed by a second PBKDF2 derivation with its own random salt and a distinct label. A wrong passphrase or any modification fails **before a byte is decrypted**, with one fixed message ("the passphrase is wrong or the bundle was modified", exit 4); a flipped byte in a data file is reported by file name. Alternatives considered: `age` and `gpg` (not installed here or in the image; a new system dependency); AES-GCM through the already-present `cryptography` package (needs a chunked framing format that would be our own cryptographic construction, to be maintained and reviewed, where `enc` plus HMAC is a few lines on a standard format any `openssl` can open); no encryption (kept as the default so the bundle is inspectable; the docs say bundles hold `server_secrets` and are as sensitive as the database, so encrypt at rest or keep them on encrypted storage). Known weakness, accepted: CBC with PBKDF2 and a separate MAC is an old but sound composition; the iteration count is recorded, so it can be raised without a format change. The manifest of an encrypted bundle stays readable and lists artifact keys, sizes and hashes (metadata the product already treats as non-secret).

### 4. Restore rules (`python -m abb_api.backup restore`)

Loads into an **empty database and an empty (or new) artifact directory only**; there is no override flag and the tool never drops, truncates or overwrites. Order: verify checksums (and the MAC) -> refuse a bundle whose schema revision the code does not know (a newer bundle) -> refuse a database that has any table in `public`, a target server older than the bundle's, an artifact directory that is not empty -> make sure the roles `abb_runtime` and `abb_retention` exist (created `NOLOGIN` if the role may; otherwise refuse with the instruction) -> decrypt into a private `0700` work directory (removed in `finally`) and check the plaintext hashes -> scan the whole tar against the manifest (every member, size and hash) **before** touching the database -> `pg_restore --no-owner --single-transaction --exit-on-error` (one transaction: a failure leaves the database empty) -> unpack the artifacts -> compare the row counts, the schema revision and the existence of every artifact row's file with the manifest (exit 6 and "discard this database" on any mismatch) -> `alembic upgrade head` (a no-op at head; otherwise it upgrades an older bundle, and it applies `ABB_RUNTIME_DB_PASSWORD` / `ABB_RETENTION_DB_PASSWORD` to the roles as `make migrate` does). Privileges are restored from the dump's ACLs (`--no-privileges` is *not* used): the drill test asserts the table, sequence and column grants are identical to the source. Owners are not restored (`--no-owner`): objects belong to the restoring role, which must be the same owner role name the grants refer to. Roles and their passwords are cluster state and are not in the bundle. If a step fails after the database was loaded, the message says so and says to discard it; if the artifact unpack fails, the files this run wrote are removed.

### 5. The PostgreSQL tools

`pg_dump`/`pg_restore` come from `postgresql-client`, now installed in the API image (with `openssl`) on the Debian base (client 17, which dumps and restores PostgreSQL 16). The tool refuses a `pg_dump` older than the server. Chosen over running the dump through the postgres container: the API image is where the tool and its configuration live, the compose `backup` service (profile `ops`) reaches the same database over the network as any operator would, and nothing needs the Docker socket. The password reaches the tools only through `PGPASSWORD` in the child's environment; tool output is scrubbed of the password and of connection URLs before it is shown.

### 6. The migration safety table

`apps/api/migrations/safety.json` classifies every revision: `reversible` (the downgrade is held to the schema-and-privilege fingerprint of the previous revision by `tests/test_migration_safety.py`, which walks the whole chain once, in-process, up and down; it removes only what the revision created, deletes or rewrites no earlier row, cannot fail on data and weakens no protection) or `forward-only` (rollback is **restore the pre-upgrade backup**). Seven are forward-only: 0043 (narrowing can overflow), 0044 (deletes artifact rows), 0091 (deletes findings), 0093 (rewrites audit rows, drops approvals), 0094 (drops the event-id and alert keys), 0096 (remaps skip reasons), 0102 (loses the trace marker once retention enforced). Per-revision notes carry what a downgrade discards and lock-heavy or rewriting upgrade steps (0006/0009 plain index builds, 0041 and 0098 backfills, 0091 index under lock). The test fails if a file has no entry, an entry has no file, a class is unknown, a forward-only note does not say "restore", a reversible downgrade is empty, or a reversible downgrade does not restore the previous fingerprint. A new migration is classified in the same commit (`docs/runbooks/migrations.md`).

### 7. Alert-key rotation (`python -m abb_api.rotate alerts [--new-key] [--dry-run]`)

Two separately safe facts: a newer `alerts-encryption` row (`--new-key`, under the sealing advisory lock, as the owner role per ADR-150), and every blob re-sealed under it. A destination is one transaction: the row is locked `FOR UPDATE` (a concurrent edit waits), both blobs are opened with the key named in their own header, re-sealed with the same associated data (purpose, workspace, destination, rebuilt from the row), the new blobs are opened again and compared with the old plaintext, and only then written along with `key_id`. A blob already under the newest key is skipped, so a rerun resumes an interrupted run and a second run changes nothing (not even a nonce). A destination that cannot be re-sealed is reported by id and does not stop the others (exit 1). Old blobs keep opening until their own commit, so delivery during a rotation always decrypts. Processes cache the current key: **after `--new-key` restart the API and worker, then run `rotate alerts` once more** to catch anything sealed under the old key meanwhile; the report counts blobs per key id from the headers and says `old_keys_can_be_retired` only when no blob of any destination references a key but the newest and no destination failed. Old key rows are never deleted by the tool (the runtime role cannot, and retirement is a deliberate owner action, safe only after the report says zero and a backup postdating the rotation exists). `--dry-run` reads and writes nothing. No plaintext is returned, logged or printed: output is counts and destination ids.

### 8. What cannot be rotated

The **event-id key** (`server_secrets` name `event-id`) derives the ids of server-written `policy.*` and `approval.*` events with HMAC. A newer row would change the id derived for the same material, so a retried decision or approval event would no longer deduplicate against the stored one. It is documented as not rotatable without breaking that dedupe; if it leaks, rotate in a quiet period and accept that in-flight retries may duplicate. Other secrets (API keys, sessions, OIDC client secret, SMTP password, the runtime and retention database credentials) rotate by their own procedures, indexed in `docs/runbooks/secrets.md`.

### 9. Recovery statements

**RPO = the backup interval** (a logical dump at a point in time; nothing is captured between dumps). **RTO = the measured restore time**, recorded with the Docker-stack drill (step 6); the tool prints the restore's elapsed seconds. WAL archiving / point-in-time recovery is the documented upgrade path when a smaller RPO is needed (it is what managed PostgreSQL provides; spec §122's 15-minute target presumes it) and is out of scope.

### 10. Not covered

The operator's `.env` and secrets outside the database (OIDC client secret, SMTP password, role passwords, the passphrase) are not in the bundle and must be kept by the operator's own secret store. Role logins, PostgreSQL configuration, extensions beyond the schema, search indexes (rebuildable from events), the web build, and anything on the artifact volume that is not a store key. Backups are full, not incremental.

## Decisions taken without the user (small; recorded here)

- Always a tar for artifacts (one code path; both plain and encrypted).
- No override for a non-empty restore target (the plan said "unless an explicit guard": there is none; an operator creates a new database).
- `ABB_RESTORE_DATABASE_URL` has no fallback, so a restore can never be pointed at the production database by an unset variable.
- Create connects with `ABB_BACKUP_DATABASE_URL`, else the migration (owner) URL, else `ABB_DATABASE_URL`; a role that cannot read every table makes `pg_dump` fail, loudly.
- Passphrase minimum 12 bytes; the file must be mode 0600 at most.
- Restore creates the two application roles `NOLOGIN` when absent and allowed.
- Key-table counts, not all tables, are compared after a restore (counting the largest tables is the slow part of a restore check); `pg_restore` itself fails on a corrupt archive.
- Rotation: `--new-key` is explicit (an interrupted run is resumed without it, so rerunning never piles up key rows).

## Consequences

An operator has a documented, tested path to back up, move and recover an installation, and to say which upgrade can be undone. The bundle is exactly as sensitive as the database. A restore cannot damage an existing installation. The full-stack proof (Docker, the CI job, the recorded times) is step 6; until then the Dockerfile and compose changes are `UNVERIFIED (env)`.

Security notes: the owner credentials are required (the tool is operator-only and never imported by the API); `PGPASSWORD` is visible to the same user's `/proc` for the tool's lifetime; an unencrypted bundle's manifest is unauthenticated (checksums detect corruption, not a malicious replacement of the whole directory) and the dump inside contains `server_secrets` in the clear; the work directory of an encrypted restore briefly holds the decrypted dump (`0700`, removed afterwards; use `--workdir` on encrypted storage).
