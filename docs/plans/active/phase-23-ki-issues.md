# Phase 23: GitHub issues to file

For the owner to file (the builder does not create issues). Each: labels `known-issue` plus the severity label; the KI row in `docs/KNOWN_ISSUES.md` says "to be filed" until the number is known, then replace it with the issue link. Closing: issue #205 (KI-188) closes with the Phase 23 PR; KI-220, 222, 243, 244, 245 and 073 stay open with updated targets (their issues are unchanged).

## KI-310: development stack on PostgreSQL 16 cannot restore a bundle through the `backup` service
Labels: `known-issue`, `S3`

The API image ships a PostgreSQL 17.11 client. Its `pg_restore` sends `SET transaction_timeout`, which a PostgreSQL 16 server rejects, so a bundle created by the compose `backup` service cannot be restored through that service into the development database (found by `scripts/hosting-backup-e2e.sh`). The hosting overlay pins PostgreSQL 17 (ADR-178 Amendment 1); the development and CI stack is on 16. Phase 19b's drill used native tools matching the server, so only `create` and `verify` ever ran in the container.

Impact: development and CI installs cannot use the `backup` service for a restore. Fix: install a 16 client in the API image, or move `docker-compose.yml` to 17 (an `apps/`/base-compose change; run the migration and backup-restore e2e). Target: before anyone relies on the compose `backup` service for restore on the development stack.

## KI-311: web read proxy drops X-Forwarded-*, so per-address session-failure throttling is coarse through the relay
Labels: `known-issue`, `S4`

`/api/abb/*` forwards to the API without the browser's `X-Forwarded-*` headers, so the API sees the web container as the client for session traffic. Failed-session throttling per address does not apply through the relay; it is bounded by the global ceiling (600 failures per window) and the web server's own cost. Direct `/v1/*` traffic (the SDK) is throttled per address. Measured in the Phase 23 rig: after 620 junk cookies a valid session was still served (`relay.junk_flood_keeps_valid_session`), so real users are not locked out. Plan open question U2, accepted.

Fix when a measured gap appears: the relay forwards a trusted client address, with a fail-before test and an ADR amendment. Target: with the next proxy change or a measured user-visible gap.

## KI-312: no scripted cut-over to restore a bundle into a hosted install
Labels: `known-issue`, `S3`

`scripts/hosting-restore-drill.sh` proves a bundle restores into a throwaway database. Repointing `.env` (database URLs, artifact directory) at a restored database on the server is a manual procedure (`docs/runbooks/hosting-operations.md#restoring-onto-a-new-server`) that has not been performed on a real server (`UNVERIFIED (env)`).

Fix: perform it once during the owner's gated run (G2), record the commands, then script a promote step or correct the runbook. Target: Phase 23 gate G2.

## KI-313: hosting rigs are not in CI
Labels: `known-issue`, `S4`

`make hosting-e2e`, `hosting-backup-e2e`, `hosting-upgrade-e2e`, `hosting-bootstrap-e2e` and `hosting-obs-e2e` need Docker with Compose 2.24 or newer and several minutes each, and the heavy ones must not run in parallel. Changes to the overlay, the Caddy site or the scripts are only checked when someone runs them by hand. The memory ceilings were measured with small data.

Fix: a nightly or manual-dispatch workflow once the runtime on a hosted runner is measured; re-measure the peaks with realistic data before trusting or raising the limits (the retention ceiling was set while it ran in `dry_run`). Target: after the first live deployment.

## KI-314: no host dashboard and no external uptime check in the hosting monitoring
Labels: `known-issue`, `S4`

Host, backup and certificate alerts exist and are tested, but there is no host dashboard; the blackbox probe runs from the server, so an outage visible only from outside is found by `hosting-verify.sh --server-probe` from another machine rather than alerted. Fix: a host dashboard and a documented free external uptime check once the first live run shows which panels are used. Target: after the first live deployment.

## KI-315: the dashboard behind the hosting Caddy is not executed in a browser by the rig
Labels: `known-issue`, `S4`

`hosting-verify.sh` follows sign-in, cookies, CSP and streams with raw requests; no browser runs the dashboard's JavaScript behind the proxy (the https cookie behaviour has a browser check in `auth-https-e2e.sh`). A proxy setting that only breaks client-side scripts would not be caught locally. Fix: a Playwright spec in the rig, or one browser pass at the owner's first deployment. Target: Phase 23 gates G1/G2.
