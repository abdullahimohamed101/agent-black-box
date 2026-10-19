# PR draft: Phase 20, demo, docs, release quality

Status: merged into `main` as PR #300 (2026-10-19).

## Title

feat(release): Phase 20 - Apache-2.0, KI-097, scripted demo, brand, landing site, docs, release checklist, SDK import rename

## Summary

Prepares Agent BlackBox for a public open-source launch. Nothing in this PR publishes anything: publication (gate G2) is parked by the owner and every gated claim stays gated in the default build.

By area:

- **License and community.** Apache-2.0 for the whole repository (root `LICENSE`, `NOTICE`, SPDX metadata in every package), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, issue and pull request templates (ADR-175).
- **KI-097 (live run health).** A running run shows the last completed analysis labelled "as of N s ago" instead of `no_data`; `detect_findings` is throttled (`ABB_FINDINGS_MIN_INTERVAL_SECONDS`, terminal runs bypass it); additive API fields `as_of`, `stale`, `server_time` (ADR-174). Deterministic end-to-end stage in `scripts/findings-e2e.sh`.
- **Examples and `make demo`.** Coding, simple custom and multi-agent examples run scripted and deterministic by default (no API key, no network); `--live` is optional on coding and custom. `make demo` / `make demo-reset` / `scripts/demo-e2e.sh` (run 1 == run 2 after normalisation), project `abb-demo`.
- **Brand.** Pulse mark as the launch glyph; `scripts/brand.sh` generates favicons, social card, icon and README header.
- **Screenshots.** `scripts/screenshots.sh` regenerates the release set from the real stack with demo data (6 views, light and dark); KI-123: Playwright specs no longer write into tracked `docs/screenshots` (`shotsDir()`).
- **Landing site.** `site/` is a Next static export with no third-party requests, a check suite (`scripts/check-site.sh`, 14 subchecks) and an inert Pages workflow.
- **Docs.** `docs/README.md`, `quickstart.md`, `deployment.md`, `troubleshooting.md`, a publication runbook, a docs command runner and a link checker.
- **README.** Rewritten for launch; five `RELEASE_CLAIM` markers keep gated claims out of the default build.
- **Release checklist.** `scripts/release-checklist.sh` (placeholders, debug output, secrets, charts, routes, network, shots, claims, links, paths, imports, licenses) with planted-violation tests; `check-pins` now rejects one `repo:tag` with two digests (KI-265).
- **SDK rename and guard_push.** Import package `blackbox` -> `agent_blackbox` (ADR-177; breaking but unpublished). `guard_push` carries the classifier's risk class, R3 for forced pushes (ADR-176).
- **Demo film design.** Briefs, storyboard and QA report for a silent, captioned film. The video itself is not in git.

## Decisions

ADR-174 (live health, `as_of`/`stale`, cadence), ADR-175 (Apache-2.0), ADR-176 (`github.push` carries the risk class), ADR-177 (`agent_blackbox`). Plan decisions D1-D18 (D1-D6, D15-D17 by the user; D18 by the owner lifts the no-JavaScript constraint so the site is a Next static export) are in `docs/plans/completed/phase-20-release-polish.md`.

## Evidence

Tip `ae3e63a` before the docs close-out; lead's final verification 2026-10-19.

| Check | Result |
| --- | --- |
| API suite (CI-style: otel extra, `ABB_DATABASE_URL`, `abb-pg-test`) | 4336 passed, 1 skipped, 2 failed: the load-sensitive outbox lease tests (KI-309), fixed test-side in `6ad2b7d`, `ae3e63a` (file 21/21 on 5 runs; 30/30 under CPU load per the fixer). Full suite NOT re-run after the fix. |
| Fixtures Playwright | 821 passed, 123 skipped, 0 failed (8.5 min, clean machine). Two later full runs on a throttled battery laptop (~17 min) showed 1 and 4 timing failures in different tests, all passing when re-run alone: flaky under CPU starvation, not regressions. |
| Web vitest | 1739/1739 at default timeouts earlier; 105 files / 1739 tests with `--maxWorkers=2 --testTimeout=60000` on the throttled machine |
| SDK suite | 1285 passed |
| `scripts/check-site.sh` | all 14 subchecks passed |
| `scripts/demo-e2e.sh` | DEMO-E2E PASSED; run 1 == run 2 (normalised); the push request carries class R2 |
| `scripts/release-checklist.sh` | clean; `--release` fails only on the 5 README `RELEASE_CLAIM` markers (by design) |
| `check-pins.sh`, `audit-lockfiles.sh` | OK; 8 lockfiles, 286 pinned packages |
| Docs | links 0 broken in 212 files; `docs-commands-check.sh` 0 problems |
| Scripts unittest | 53 OK (1 skipped by design) |
| Static analysis | tsc, eslint, prettier, `gen:api:check` clean; mypy and ruff clean (API, SDK, event-schema) |
| `scripts/screenshots.sh --verify` | verified twice |
| Real-stack e2e | coding-e2e and an sdk-e2e equivalent passed after the rename |

## Not verified

- `scripts/quality.sh full` (needs the full local environment): `UNVERIFIED (env)`.
- A clean post-fix full API suite and a clean full Playwright run: `UNVERIFIED (env)`; CI is the arbiter.
- The rendered-app crawl and the product request log (KI-306): AC15 product half open.
- Safari and Firefox playback of the video formats.
- CI jobs `release-checklist`, `site-check`, `demo-e2e` on GitHub; `--live` modes (never run, KI-044).
- The final timing runs were on a throttled, battery-powered laptop; this is why timing tests flaked.

## Parked (not in this PR's scope)

Publication (G2): removing the README claim markers, enabling Pages, making the repository public, tagging and publishing packages (`docs/runbooks/release-publication.md`). The demo video's claims-variant and site embedding. The hosting guide is a separate later plan.

## Migration and compatibility notes

- SDK import rename `blackbox` -> `agent_blackbox` is breaking but the package is unpublished; update any local scripts (a checklist guard rejects the old import).
- API changes are additive: health dimensions gain `as_of` and `stale`, run findings gain `server_time`.
- Policy dry run maps `git.push` to R2; enforcement may send R3 for a forced push (KI-308).
- `make demo` creates the project `abb-demo` and its own key in workspace `local`; it does not touch the `demo` project or the dev key. `make demo-reset` removes it.
- `ABB_FINDINGS_MIN_INTERVAL_SECONDS` is new (default throttle; `0` disables).
- No database migration, no event-contract change.

## Known issues from this phase

Open: KI-306 (crawl and request log, before G2), KI-308 (force-push dry-run parity), KI-301..304 (design pass 2 busy-screen items), KI-297..300 (stress-test follow-ups). Resolved: KI-097, 119, 123, 129, 265, 305, 307, 309. Issues are filed in the close-out docs PR after merge.

🤖 Generated with [Claude Code](https://claude.com/claude-code).
