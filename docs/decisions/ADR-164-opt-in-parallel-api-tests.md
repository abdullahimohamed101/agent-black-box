# ADR-164: Parallel API tests (pytest-xdist): opt-in locally, used by CI

Status: Accepted; amended 2026-10-20: CI and `scripts/quality.sh` now run the API tests in parallel (see "Amendment 1")
Date: 2026-10-16
Phase: tooling (no phase)

## Context

The API suite (about 3,700 tests) takes 26-30 minutes serially. It is bound by I/O waits (per-test `TRUNCATE`, `fsync`) inside a 2-CPU, 4 GB Docker VM, not by CPU.

## Decision

* **pytest-xdist is a dev dependency of `apps/api`, used only on request.** The default `make test-api`, `scripts/quality.sh` and CI stay serial: the CI runners have 2 cores, a serial run is the reference that proves correctness, and parallelism adds a class of failures (shared state, timing under load) that a gate should not inherit without evidence. `make test-api-fast` (`-n N`, default 2) is the opt-in.
* **Worker isolation:** one scratch database and one runtime role per worker, named with the worker id; the cluster-wide `abb_runtime` role is provisioned under an advisory lock before migrations; test migrations never inherit `ABB_RUNTIME_DB_PASSWORD`.
* **A separate throwaway server** (`docker compose --profile testdb`, port 5434, tmpfs, `fsync=off`) for the fast target, because on the durable Compose database fsync waits dominate and extra workers add little. It is never the dev database and loses its data on restart.
* No test is serialised with `xdist_group`; none needed it.

## Consequences

Local runs can be several times faster; the numbers on a quiet machine are recorded in `docs/TESTING.md`. Test ids must be deterministic across workers. Making xdist the default in CI is a separate decision that needs measured, repeated parallel runs equal to the serial count.

## Amendment 1 (2026-10-20): CI runs the API suite in parallel

The condition in the Consequences section was met on today's suite (4,357 passed, 1 skipped): five full parallel runs after the first fixes (`-n 3` once, `-n 4` four times, one of them on a freshly created server) produced the same pass count (the freshly created server run had one failure, the scrape test below, fixed and then not seen again in two more runs), and the failures found (two tests that called `alembic()` inside a running event loop, one scrape test that assumed the first scrape beat a fixed wait, one unpinned test image) were test or tooling defects, fixed without weakening any test. Measured on the same 2-CPU Docker VM and fsync-off server: serial 24 min 50 s; `-n 3` 11 min 6 s; `-n 4` between 8 min 49 s and 11 min 24 s over four runs (host load). One early `-n 3` attempt produced a long run of setup errors before a pass count could be taken; it was killed, not diagnosed, and did not recur in seven later runs.

* **CI:** the `quality` job sets `ABB_API_TEST_WORKERS=4` (the `ubuntu-latest` runner has 4 vCPUs and 16 GB). `scripts/quality.sh` turns that into `pytest -n 4`; each worker already creates its own database and role on the service Postgres, so no further isolation is needed. A step before the tests raises the service container's `max_connections` to 300 (peak observed with 4 workers: 65, default 100) and turns fsync off, then restarts it.
* **Switch:** `ABB_API_TEST_WORKERS` is a number, `auto`, or `0`/`serial`. Default 4 when `CI` is set, 2 elsewhere. `make test-api` stays serial and is the reference run.
* **Coverage:** the API suite has no coverage gate (only the SDK and integrations do, and those stay serial), so there is nothing to combine.
* The first-run reasoning for keeping CI serial (two cores per runner) no longer holds: GitHub's standard Linux runners have four.

## Amendment 2 (2026-10-22): the fresh-cluster role race

The first CI run (a fresh Postgres service) failed with about a third of the suite in setup errors: migration 0101's `CREATE ROLE abb_retention` raced between workers (unique violation on `pg_authid`, not `duplicate_object`). Amendment 1's local runs never saw it because the throwaway server was long-lived. Test-side fix only (migrations unchanged): an advisory lock around every `alembic()` call plus a session-wide pre-creation of every cluster-global role, a guard test that fails when a migration creates a role the harness does not know, and `scripts/migration-race-check.sh` (fresh containers, N processes at once; 10/10 rounds failed before, 0/10 after). Parallel changes must be verified on a new empty server.
