# Private hosting recipe (Phase 23, ADR-178)

Files for running Agent BlackBox on one small server behind a shared Caddy that obtains HTTPS certificates. This directory is a
recipe, not a certified product: the local rig (`scripts/hosting-e2e.sh`) proves the proxy chain against Caddy's local CA, and
live issuance, real Google sign-in and real server hardening stay `UNVERIFIED (env)` until the owner runs them.

Plan: `docs/plans/active/phase-23-private-hosting.md`. Decision: `docs/decisions/ADR-178-private-hosting-topology.md`.
