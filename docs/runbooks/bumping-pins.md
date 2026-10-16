# Bumping pins by hand

Every GitHub Action is pinned by commit sha and every container image by multi-arch index digest
(KI-024, ADR-173). Dependabot (`.github/dependabot.yml`) opens weekly pull requests that move the
pin and its readable tag together. This page is for the cases it does not cover, and for checking a
pin yourself. `scripts/check-pins.sh` (`make check-pins`, CI job `pins`) fails when something is
left unpinned.

## Verify what is pinned

```bash
make check-pins                 # actions by sha, images by digest, tag comment present
make audit-lockfiles            # pip-audit over every uv.lock (needs network)
```

## An action

Pin the commit the tag points to now, keep the tag in a trailing comment.

```bash
gh api repos/OWNER/REPO/git/ref/tags/TAG --jq '.object.type + " " + .object.sha'
# type "commit": that sha is the answer.
# type "tag" (annotated): follow it to the commit:
gh api repos/OWNER/REPO/git/tags/SHA --jq .object.sha
# the sha must exist in the repo:
gh api repos/OWNER/REPO/commits/SHA --jq .sha
# which exact release is it (for the comment)?
gh api "repos/OWNER/REPO/git/matching-refs/tags/vMAJOR." --jq '.[] | select(.object.sha=="SHA") | .ref'
```

Write `uses: OWNER/REPO@<40-hex sha> # vX.Y.Z`. A major tag such as `v4` moves; the sha does not.
Never change the major version while pinning. Resolve a branch ref (`@main`) the same way with
`git/ref/heads/main`.

## An image

```bash
scripts/resolve_image_digest.py postgres:16-alpine
# prints postgres:16-alpine@sha256:<digest>   (the multi-arch index, so arm64 and amd64 both resolve)
docker manifest inspect postgres:16-alpine@sha256:<digest> >/dev/null && echo resolves   # no pull
```

Write `image:tag@sha256:...` (keep the tag: Dependabot and readers use it). Pinned places:
both Dockerfiles (`FROM` and `COPY --from=`), `docker-compose.yml` (postgres, prometheus, grafana)
and the `services:` images in `.github/workflows/ci.yml`. `scripts/obs_e2e_prom.py` names the
postgres image once as a local helper and must carry the same digest.
Do not use a per-platform digest (it breaks the other architecture): the resolver refuses one.
After a bump run `docker compose config -q`, `docker compose --profile observability config -q` and
`docker compose --profile ops config -q`.

## The audit tool and allow-lists

`scripts/audit-lockfiles.sh` runs `pip-audit` at the version set in the script (`uvx`, never a project
dependency); Dependabot cannot see that pin, so bump it by hand now and then. A finding fails the job.
If it has a fix, bump the package in the right project (`uv lock --upgrade-package NAME`, then that
project's tests). If there is no fix, or it cannot be taken yet, add a reviewed line to
`scripts/audit-allowlist.txt`: `ID | package | YYYY-MM-DD | reason`. The date must be within 180
days; an expired line fails the audit so the exception is looked at again, and a line that matches
nothing is reported as stale. Exceptions to the pin check (`agent-black-box-api:local`, built by
compose) live in `scripts/pins-allowlist.txt` with a reason.
