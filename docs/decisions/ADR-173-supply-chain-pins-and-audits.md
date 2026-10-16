# ADR-173: Supply chain: sha and digest pins with the tag kept, a pinned uvx audit tool, and a dated allow-list

Status: Accepted (Phase 19c, step 7). Implements D8 of the Phase 19c plan; the choices were made by the assistant under the standing instruction and are listed so they can be reversed.
Resolves KI-024 and KI-060. Spec 127 (dependency management and supply chain).
Date: 2026-10-16
Phase: 19c

## Context

CI ran third-party actions by moving tags and pulled base images by moving tags, so whoever controls a tag controls what runs with the repository's token and what ships. The Python lockfiles of the integrations were never audited.

## Decision

1. **Actions are pinned by the 40-hex commit sha with the release tag in a trailing comment** (`uses: actions/checkout@<sha> # v4.4.0`). The sha is the commit the same tag (`v4`, `v5`) pointed to when pinned: major versions were not changed. The comment carries the exact release the sha equals, so a human and Dependabot can read it.
2. **Images are pinned `image:tag@sha256:<index digest>`**, tag kept. The digest is the multi-arch index, not a per-platform manifest, so arm64 laptops and amd64 CI both resolve it (`scripts/resolve_image_digest.py` refuses a non-index). Applies to `FROM`, `COPY --from=`, compose services and CI `services:`.
3. **A script fails the build on anything unpinned** (`scripts/check_pins.py`, CI job `pins`, `make check-pins`): it counts a digest in a comment as unpinned, treats earlier build stages and `scratch` as non-images, and flags a pinned image named again without its digest (the failure mode of bumping five places and forgetting the sixth). Exceptions are a reviewed file with a reason (`scripts/pins-allowlist.txt`; one entry, the locally built API image).
4. **Dependabot** bumps both (github-actions, docker, docker-compose, npm, uv), weekly, minor and patch grouped per ecosystem to limit noise, majors alone.
5. **`pip-audit` runs through `uvx` at a pinned version**, never as a project dependency (the SDK has a near-zero dependency budget; the API should not ship an auditor). Requirements are exported from each frozen lock with hashes and audited without resolving anything new, so what is audited is what is installed. Policy is in a small tested script: any finding fails, with or without a fix; an exception is a line in `scripts/audit-allowlist.txt` with advisory id (or alias), package, **expiry (at most 180 days)** and reason. Expired lines fail, stale lines are reported. Failing also on unfixed findings is deliberate: "no fix" is a decision someone should write down with a date.
6. **A weekly run of the audit only** (`.github/workflows/lockfile-audit.yml`) so a new advisory does not first surface on someone's unrelated pull request. The main workflow was not given a `schedule:` because that would run all 22 jobs weekly.

## Alternatives considered

- **Tag pins with Dependabot only.** Rejected: that is today's trust model (a tag can be moved after review).
- **Digest only, no tag.** Rejected: unreadable and Dependabot cannot tell what version it is moving from.
- **Per-platform digests.** Rejected: break one architecture.
- **`uv audit` / `pip-audit` as a dev dependency.** Rejected: dependency budget, and it would be audited by itself.
- **Allow-list without expiry.** Rejected: silent permanent exceptions.
- **Run `pnpm audit` for dev dependencies too.** Unchanged (KI-010).

## Amendment (Phase 19c adversarial review)

`uvx pip-audit==<version>` pins pip-audit itself but resolves pip-audit's own dependency tree at run time, so those transitive dependencies are not pinned or hash-checked. Blast radius: the audit job has `contents: read`, writes only a local report, and holds no secret, so a hostile transitive release could at worst forge or hide a finding, not reach the repository or the images. The follow-up is a hashed lock for the tool (`uvx --with-requirements` against a `requirements.txt` with hashes, bumped with the version), which needs network access to generate and was not produced here (KI-268). Nothing in the shipped scripts changed.

## Consequences

Updates are now PRs that must be reviewed; a wrong pin only shows when CI runs (KI-267). The audit tool's version and one helper-script digest are hand-bumped (KI-265). The checker reads text, so run-time-built image names and OS packages are not covered (KI-266). A new advisory can fail an unchanged tree; that is the point of the weekly run.
