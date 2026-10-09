#!/usr/bin/env bash
# A fake OpenID Connect provider for development, compose and the browser login test.
# DEVELOPMENT ONLY: it accepts any email and signs tokens with a throwaway key. Never expose it.
#   scripts/fake-oidc.sh [port]       (default 8900; client id abb-dev)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
port="${1:-8900}"
origin="${ABB_WEB_ORIGIN:-http://localhost:3000}"
cd "$root/apps/api"
exec uv run python -m tests.fake_oidc --port "$port" --client-id "${ABB_OIDC_CLIENT_ID:-abb-dev}" \
  --redirect-uri "${origin%/}/api/auth/callback"
