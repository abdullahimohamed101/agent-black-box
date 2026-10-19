# Runbook: publishing Agent BlackBox (gate G2)

Run this only after the Phase 20 pull request is merged and CI on `main` is green. Every step that cannot be undone
cleanly says so. Nothing here is automatic: the owner decides the moment.

## 0. Preconditions (all must hold)

- `main` contains Phase 20 and its CI run is green.
- `scripts/release-checklist.sh` passes. `scripts/release-checklist.sh --release` fails **only** on the five
  `RELEASE_CLAIM` markers in `README.md` (step 1 removes them): any other hit (a `[CONFIRM ...]` marker, a site
  `confirm("AFTER ...")` call, a placeholder, a local path) is a defect to fix first.
- With the flag on, the site export passes `scripts/check-site.sh <export-dir> --static --release --only build --only links --only size --only claims`
  (what `.github/workflows/pages.yml` runs). `--release` must fail on every one of: a `[CONFIRM ...]` marker left in the
  export, a gated claim (open source, self-hosted, a GitHub link, `--live`, an adapter) present while the flag is off, and a
  missing release-only fact. Run it on the flag-on export before step 1; a pass here is what lets the Pages deploy succeed.
- The history audit below was run on the exact commit to be published and found nothing (it must be repeated: history grows).
- The owner has read the "Open decisions" below and answered them.

### History audit (run before the repository becomes public)

History becomes public with the repository. Check it, do not assume:

```bash
git log --all --name-only --diff-filter=A --pretty=format: | sort -u | grep -E '(^|/)\.env$|\.pem$|\.key$|id_rsa|id_ed25519|\.p12$'
git log --all -p --no-color | grep -E 'AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_|glpat-|xox[baprs]-|sk_live_|-----BEGIN [A-Z ]*PRIVATE KEY-----'   # then confirm every hit is a test fixture
git grep -I -n '/Users/' -- . ':!docs/screenshots'                                                                                    # local paths must be gone
```

Result on 2026-10-19: no env, key or certificate file was ever added; every secret-shaped string sits in redactor and
scanner test fixtures, docs, or dev defaults (`.env.example`, CI, Makefile). Two tracked files held a local absolute path;
both were fixed.

## 1. Flip the gated claims (one pull request, reviewed)

1. In `README.md` resolve each `RELEASE_CLAIM` comment as its text says (tagline, clone URL, install line, third-party
   claim, licence section) and delete the comments.
2. Build the site with the flag on: `ABB_RELEASE_CLAIMS=1` (the Pages workflow sets it for its deploy; check
   `.github/workflows/pages.yml`).
3. `scripts/release-checklist.sh --release` must now pass with no failures, and `scripts/check-site.sh` with the flag
   on must pass (the site checks assert the gated words appear only when the flag is on).
4. Merge. (The repository is still private at this point; nothing is published yet.)

## 2. Make the repository public (not cleanly reversible)

Settings, General, Danger Zone, Change visibility. Before clicking: enable Settings, Code security: private
vulnerability reporting (SECURITY.md points at it), secret scanning and push protection, and Dependabot alerts.
Turning a repository private again later does not remove copies that others have already taken.

## 3. Enable GitHub Pages

Settings, Pages, Source: GitHub Actions. Then run the `pages` workflow (`workflow_dispatch`). It is inert until Pages is
enabled. Confirm the site loads at the project URL under `/agent-black-box/` and that `scripts/check-site.sh network`
passes against it.

## 4. Tag

```bash
git switch main && git pull --ff-only
git tag -a v0.1.0 -m "Early release. No stability promise: the event schema, HTTP API and SDK may change."
git push origin v0.1.0
```

Create a GitHub release from the tag with the README's "Project status" limits copied in. No package is published.

## 5. After publishing

- Record the claims variant of the film (`RELEASE_CLAIMS=1`; stored unpublished until now) and add the video to the site
  (`site/public/assets/video/`, the Watch section appears automatically).
- Post only what the positioning brief allows. Reply to every comment in the first hour.

## Open decisions (owner)

1. Go-live moment. Recommended: a Tuesday to Thursday morning US Eastern, after a final dry run of the quickstart on a
   clean machine (the "self-hosted" and "no API key" claims are only verified by that run).
2. Package names. See `docs/design/launch/package-names.md`.
