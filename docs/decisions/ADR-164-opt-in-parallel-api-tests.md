# ADR-164: Opt-in parallel API tests (pytest-xdist) on a throwaway Postgres

Status: Accepted
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
