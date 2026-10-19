#!/usr/bin/env bash
# Writes container restart count, out-of-memory kill and running state for node_exporter's textfile collector (Phase 23, step 9).
# Run every minute by abb-container-metrics.timer. Restart: unless-stopped hides a crash loop behind a "running" status and Docker only
# records an OOM kill on the container itself, so this is where those two become visible to Prometheus (alerts in deploy/hosting/rules/host.yml).
# Reads `docker inspect` only; no container is touched; writes one file atomically (mode 0644: no secret in it).
set -euo pipefail
CONF="${ABB_BACKUP_ENV:-/etc/abb/backup.env}"
# shellcheck disable=SC1090
if [ -r "$CONF" ]; then set -a; . "$CONF"; set +a; fi
TEXTFILE_DIR="${ABB_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# ABB_METRICS_PROJECTS (space separated compose project names) limits the report to those projects; unset means every container on the host.
if [ -n "${ABB_METRICS_PROJECTS:-}" ]; then
  ids=""
  for project in $ABB_METRICS_PROJECTS; do ids="$ids $(docker ps -aq --filter "label=com.docker.compose.project=$project")"; done
  ids="$(echo "$ids" | tr ' ' '\n' | sort -u | tr '\n' ' ')"
else
  ids="$(docker ps -aq)"
fi
# OOM kills of the last window, from the daemon's event log (State.OOMKilled is reset by the automatic restart; review P2-5).
events="$(mktemp)"; trap 'rm -f "$events"' EXIT
docker events --since "${ABB_OOM_WINDOW:-15m}" --until "$(date +%s)" --filter type=container --filter event=oom \
  --format '{{.Actor.Attributes.name}}' >"$events" 2>/dev/null || true
if [ -z "${ids// /}" ]; then printf '[]' | python3 "$HERE/hosting_backup_lib.py" containers --file "$TEXTFILE_DIR/abb_containers.prom" --oom-events "$events"; exit 0; fi
# shellcheck disable=SC2086 # one argument per container id, on purpose
docker inspect $ids | python3 "$HERE/hosting_backup_lib.py" containers --file "$TEXTFILE_DIR/abb_containers.prom" --oom-events "$events"
