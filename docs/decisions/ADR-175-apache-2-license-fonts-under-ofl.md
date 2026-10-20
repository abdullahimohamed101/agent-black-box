# ADR-175: Apache-2.0 for the whole repository, fonts under OFL

Status: Accepted (Phase 20, step 1). Records decision D6 of the Phase 20 plan, which the user made in chat.
Date: 2026-10-18
Phase: 20

## Context

The repository is being prepared for a public open-source launch. It had no license, so by default nobody could legally use, embed or contribute to it. The SDK and the framework adapters are meant to be embedded in other people's agents.

## Decision

1. **The whole repository is Apache-2.0.** One root `LICENSE` (the canonical ASF text, verified by SHA-256 in `scripts/release-checklist.sh licenses`) and one `NOTICE`. Every `pyproject.toml` and `package.json` declares `Apache-2.0` (PEP 639 SPDX expression `license = "Apache-2.0"` for Python).
2. **The IBM Plex fonts stay under the SIL Open Font License 1.1** (`apps/web/src/fonts/OFL.txt`). They are bundled unmodified, and `NOTICE` records them as the one third-party exception.
3. **Contributions are licensed under Apache-2.0 by section 5 of the license.** No CLA and no sign-off requirement.
4. The copyright holder line in `NOTICE` is a marked placeholder until the user provides it; the release checklist fails on it in release mode.
5. `license-files` is not set in the per-package metadata: the single `LICENSE` lives at the repository root, outside each package directory, and the build backend only globs inside the project. Packaging the license into each wheel is deferred to publication (Phase 20 plan, Q4).

## Alternatives considered

- **MIT.** Simpler, but no patent grant, which matters for an SDK that other people embed.
- **Per-package licenses (permissive SDK, copyleft server).** Rejected: confusing, and no commercial model is being protected.
- **No license until later.** Rejected: it blocks the launch and every outside contribution.

## Consequences

- Any new package must declare `Apache-2.0` or `scripts/release-checklist.sh licenses` fails.
- A dependency with an incompatible license must not be added without a new ADR.
