#!/usr/bin/env bash
# Reproduces the cluster-global role race: N processes migrate fresh databases on a brand-new, empty Postgres
# cluster at the same moment (CI's condition; a long-lived server already has the roles and hides the race).
# Usage: scripts/migration-race-check.sh [rounds=10] [processes=4]   Exit 1 if any round fails.
set -euo pipefail
rounds="${1:-10}"; procs="${2:-4}"; port=5441; name=abb-pg-race
root="$(cd "$(dirname "$0")/.." && pwd)"
image="$(sed -n 's/^ *image: \(postgres:16-alpine@sha256:[0-9a-f]*\)$/\1/p' "$root/docker-compose.yml" | head -1)"
trap 'docker rm -fv "$name" >/dev/null 2>&1 || true' EXIT
failed=0
for round in $(seq 1 "$rounds"); do
  docker rm -fv "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" -e POSTGRES_USER=abb -e POSTGRES_PASSWORD=abb_dev_password -e POSTGRES_DB=abb \
    -p "127.0.0.1:$port:5432" --tmpfs /var/lib/postgresql/data "$image" -c fsync=off -c max_connections=200 >/dev/null
  until docker exec "$name" pg_isready -U abb -d abb >/dev/null 2>&1; do sleep 1; done
  sleep 2  # the image restarts once after initialisation
  until docker exec "$name" pg_isready -U abb -d abb >/dev/null 2>&1; do sleep 1; done
  pids=(); logs=()
  for p in $(seq 1 "$procs"); do
    log="$(mktemp)"; logs+=("$log")
    (cd "$root/apps/api" && ABB_TEST_DATABASE_URL="postgresql+asyncpg://abb:abb_dev_password@localhost:$port/abb_test" \
      uv run python -c "
from tests.conftest import temporary_database
with temporary_database(migrate=True):
    pass") >"$log" 2>&1 &
    pids+=($!)
  done
  bad=0
  for i in "${!pids[@]}"; do wait "${pids[$i]}" || { bad=$((bad+1)); tail -n 4 "${logs[$i]}"; }; done
  rm -f "${logs[@]}"
  echo "round $round: $((procs-bad))/$procs migrated"
  [ "$bad" -eq 0 ] || failed=$((failed+1))
done
echo "rounds failed: $failed of $rounds"
[ "$failed" -eq 0 ]
