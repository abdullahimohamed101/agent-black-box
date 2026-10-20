#!/usr/bin/env bash
# Verifies a hosted Agent BlackBox behind its reverse proxy (Phase 23, ADR-178): TLS, redirect, HSTS, nonce CSP, closed paths,
# cookies, forwarded-for handling, event streams, body limit, compression and flood behaviour. The same assertions run against
# the local rig (scripts/hosting-e2e.sh) and against a real URL. Needs python3 only. Exit 1 on any FAIL; secrets are never printed.
#   scripts/hosting-verify.sh https://abb.example.org
#   scripts/hosting-verify.sh https://abb.localhost:18443 --cacert root.crt --resolve 127.0.0.1 --http-port 18080 \
#       --stream-path /v1/runs/<id>/stream --stream-token-file key.txt --metrics-token-file token.txt --strict
#   scripts/hosting-verify.sh --config-only [--env-file F]      render the production compose files and check them (no network)
#   scripts/hosting-verify.sh --docker-project NAME             no container of the project publishes a non-loopback port
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
command -v python3 >/dev/null || { echo "hosting-verify: python3 is required" >&2; exit 2; }
exec python3 "$root/scripts/hosting_verify_lib.py" "$@"
