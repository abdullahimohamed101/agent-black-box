# Runbook: dead-lettered jobs (runs show `summary_state: "failed"`)

A job is dead-lettered after 5 failed attempts (backoff 5 s, 10 s, 20 s, 40 s). Its run keeps its events but its
summary and spans are stale or missing, and the API says so (`summary_state: "failed"`).

1. List them: `python -m abb_api.cli jobs-list --status dead_letter` (id, type, attempts, first line of the error).
   In compose: `docker compose --profile app exec api python -m abb_api.cli jobs-list`.
2. Read the cause in the worker log (`job failed`, `outcome: dead_letter`, with traceback) and the job's `last_error`.
   Typical causes: a bug in a handler or derivation rule, a database limit, a malformed job payload.
3. Fix and deploy the cause. Never edit event rows (they are immutable).
4. Give the jobs a fresh set of attempts: `python -m abb_api.cli jobs-retry` (all) or `--id <uuid>` (one). The run flips to
   `processing`, then `current`. If newer work for the same run is already pending, the dead letter is left alone and the
   pending job will recompute the run.
5. If the cause was a data problem that cannot be fixed in code, leave the job dead-lettered and note it in the incident
   record; the run's events remain queryable.

Dead letters are never retried automatically: a deterministic failure would loop forever.

## Metrics and alerts

Dashboard row: **Worker and jobs** (Dead letters panel). Alerts: `AbbWorkerJobsDeadLettered` (any job dead-lettered in 15 minutes, ticket), `AbbAlertDeadLetters` (alert notifications that exhausted their retries, ticket).

- `worker_jobs_total{outcome="dead_letter"}` counts jobs by `type` as they are dead-lettered; `processor_failures_total{type}` counts every failed attempt before that. Retried jobs are not dead letters.
- `alert_dead_letters` is the number of **alert notifications** (not jobs) that gave up; the runbook for those is [alerting](alerting.md#monitoring).
- The counters count events since the process started; the list of dead-lettered jobs that need a person is still `jobs-list` (step 1).
