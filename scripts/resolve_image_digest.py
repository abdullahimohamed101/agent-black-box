#!/usr/bin/env python3
"""Print the multi-arch INDEX digest of an image tag, without pulling it (stdlib only).

    scripts/resolve_image_digest.py postgres:16-alpine   ->  postgres:16-alpine@sha256:...

Used to pin images (docs/runbooks/bumping-pins.md). Docker Hub, ghcr.io and any registry that
speaks the Docker token flow and the registry v2 API are supported. The digest is read from the
Docker-Content-Digest header of the manifest-list/index, so arm64 and amd64 both resolve.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)
TIMEOUT = 20


def split(ref: str) -> tuple[str, str, str]:
    name, _, tag = ref.partition(":")
    tag = tag or "latest"
    first, _, rest = name.partition("/")
    if "." in first or ":" in first or first == "localhost":
        registry, repo = first, rest
    else:
        registry, repo = "registry-1.docker.io", name if "/" in name else f"library/{name}"
    return registry, repo, tag


def _open(url: str, headers: dict[str, str], method: str = "GET"):  # type: ignore[no-untyped-def]
    request = urllib.request.Request(url, headers=headers, method=method)  # noqa: S310
    return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310


def resolve(ref: str) -> str:
    registry, repo, tag = split(ref)
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    try:
        response = _open(url, headers, "HEAD")
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        challenge = error.headers.get("WWW-Authenticate", "")
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = fields.get("realm")
        if not realm or not realm.startswith("https://"):
            raise SystemExit(f"unsupported auth challenge: {challenge!r}") from error
        query = "&".join(f"{k}={fields[k]}" for k in ("service", "scope") if k in fields)
        with _open(f"{realm}?{query}", {}) as auth:
            token = json.load(auth).get("token")
        headers["Authorization"] = f"Bearer {token}"
        response = _open(url, headers, "HEAD")
    with response:
        digest = response.headers.get("Docker-Content-Digest", "")
        kind = response.headers.get("Content-Type", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit(f"no digest returned for {ref}")
    if "index" not in kind and "list" not in kind:
        raise SystemExit(f"{ref} is not a multi-arch index ({kind}); refusing to pin a platform digest")
    return f"{ref}@{digest}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for ref in argv:
        print(resolve(ref))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
