#!/usr/bin/env bash
# Monitoring E2E for hosting (Phase 23, step 9, criterion A8): runs deploy/hosting/compose.observability.yml for real on the local rig and
# validates the rules offline.
#   offline  promtool check rules (host.yml and the application rules), promtool test rules (host.test.yml, host.long.test.yml), a MUTATION
#            run (the comparison of every alert is inverted and its tests must fail), amtool check-config of the rendered Alertmanager config,
#            the runbook-link rewrite (KI-245) through amtool template render
#   live     the production overlay + the observability overlay (Prometheus 30 d / 4 GB in a named volume, node_exporter with the textfile
#            collector, Alertmanager, blackbox_exporter probing a real Caddy with its local CA, Grafana with the password from a file) beside
#            postgres, api and worker; every target UP; every rule loaded healthy; the backup and container textfiles visible; Watchdog
#            reaching Alertmanager; then a DELIBERATELY FAILED SCRAPE (worker and node_exporter stopped): AbbTargetDown and AbbHostScrapeDown
#            fire and reach Alertmanager, and resolve after the restart
# Throwaway: projects abb-hosting-obs and abb-hosting-obs-edge, ports 28080/28443/29090/29093/23001, generated secrets under .local/ (0700).
# Needs Docker with Compose 2.24+ and a local API image (agent-black-box-api:local, only COPIED to a tag of its own).
# NOT proven here (UNVERIFIED (env)): a real notification delivery (no receiver is configured: that is the owner's choice), the hairpin from a
# container to the server's own public address, node_exporter's view of a real Hetzner disk.
# shellcheck disable=SC2015,SC2001,SC2046 # test driver: A && B || fail is intended; ids are split on purpose
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
APP="${HOSTING_OBS_PROJECT:-abb-hosting-obs}"; EDGE="$APP-edge"; NET="$APP-net"; IMAGE=abb-hosting-obs-api:local
P_PROM=29090; P_AM=29093; P_GRAFANA=23001; P_HTTP=28080; P_HTTPS=28443
PROMETHEUS='prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e'
ALERTMANAGER='prom/alertmanager:v0.28.1@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba'
mkdir -p "$root/.local"; WORK="$(umask 077 && mktemp -d "$root/.local/hosting-obs.XXXXXX")"
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "HOSTING-OBS-E2E FAILED: $*" >&2; exit 1; }
made_tag=0
dc() { docker compose -p "$APP" --env-file "$WORK/app.env" -f docker-compose.yml -f deploy/hosting/compose.production.yml \
  -f deploy/hosting/compose.observability.yml -f deploy/hosting/rig/compose.obs.yml --profile app --profile observability "$@"; }
dc_edge() { docker compose -p "$EDGE" --env-file "$WORK/edge.env" -f deploy/hosting/edge/compose.yml -f deploy/hosting/rig/edge.rig.yml "$@"; }
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ] && [ -f "$WORK/app.env" ]; then
    echo "--- last log lines ---" >&2
    for s in prometheus alertmanager obs-init blackbox node-exporter grafana; do dc logs --no-color --tail 8 "$s" 2>&1 | sed "s/^/[$s] /" >&2 || true; done
  fi
  if [ "${HOSTING_OBS_KEEP:-0}" = 1 ]; then echo "left running: docker compose -p $APP down -v; docker compose -p $EDGE down -v; docker network rm $NET; rm -rf $WORK" >&2; return; fi
  docker rm -f "$APP-oomprobe" >/dev/null 2>&1 || true
  if [ -f "$WORK/app.env" ]; then dc down -v --remove-orphans >/dev/null 2>&1 || true; fi
  if [ -f "$WORK/edge.env" ]; then dc_edge down -v --remove-orphans >/dev/null 2>&1 || true; fi
  docker network rm "$NET" >/dev/null 2>&1 || true
  if [ "$made_tag" = 1 ]; then docker image rm "$IMAGE" >/dev/null 2>&1 || true; fi
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

step "preflight"
docker info >/dev/null 2>&1 || fail "docker is not reachable"
for port in $P_PROM $P_AM $P_GRAFANA $P_HTTP $P_HTTPS; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then fail "port $port is already in use"; fi
done
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker image inspect agent-black-box-api:local >/dev/null 2>&1 || fail "no agent-black-box-api:local image to copy (docker compose build api)"
  docker tag agent-black-box-api:local "$IMAGE"; made_tag=1
fi

step "O1 offline: promtool check rules (host rules and the application rules), promtool test rules, and the mutation run"
promtool() { docker run --rm --entrypoint promtool -v "$root/deploy:/d:ro" "$PROMETHEUS" "$@"; }
promtool check rules /d/hosting/rules/host.yml /d/observability/prometheus/rules/ops.yml /d/observability/prometheus/rules/slo.yml 2>&1 | grep -v '^$'
promtool test rules /d/hosting/rules/host.test.yml 2>&1 | grep -v '^$'
promtool test rules /d/hosting/rules/host.long.test.yml 2>&1 | grep -v '^$'
# a negative control for promtool itself: a syntax error must be refused, an inverted alert must fail its test
printf 'groups:\n- name: x\n  rules:\n  - alert: Broken\n    expr: up ==\n' >"$WORK/broken.yml"
if docker run --rm --entrypoint promtool -v "$WORK:/w:ro" "$PROMETHEUS" check rules /w/broken.yml >/dev/null 2>&1; then fail "promtool accepted a broken rule file"; fi
echo "promtool negative control: a broken rule file is rejected"
HOSTING_RULES_MUTATION=1 python3 -m unittest scripts/tests/test_hosting_rules.py 2>&1 | tail -4 | grep -q '^OK' || fail "the mutation run: an inverted alert still passed its tests"
echo "PASS O1 (every alert inverted in turn: its tests failed each time)"

step "generated secrets and configuration (never printed)"
rand() { openssl rand -hex "$1"; }
mkdir -p "$WORK/secrets/alertmanager" "$WORK/textfile"; chmod 700 "$WORK/secrets" "$WORK/secrets/alertmanager"
metrics_token="$(rand 32)"; printf '%s' "$metrics_token" >"$WORK/secrets/metrics_token"
grafana_pw="$(rand 16)"; printf '%s' "$grafana_pw" >"$WORK/secrets/grafana_admin_password"
chmod 600 "$WORK/secrets/metrics_token" "$WORK/secrets/grafana_admin_password"
chmod 755 "$WORK/textfile"
cat >"$WORK/app.env" <<ENV
ABB_PUBLIC_ORIGIN=https://abb.localhost:$P_HTTPS
ABB_POSTGRES_PASSWORD=$(rand 16)
ABB_RUNTIME_DB_PASSWORD=$(rand 16)
ABB_RETENTION_DB_PASSWORD=$(rand 16)
ABB_METRICS_TOKEN=$metrics_token
ABB_OIDC_ISSUER=https://idp.example.test
ABB_OIDC_CLIENT_ID=abb-obs
ABB_OIDC_CLIENT_SECRET=$(rand 16)
ABB_EDGE_NETWORK=$NET
ABB_SECRETS_DIR=$WORK/secrets
ABB_TEXTFILE_DIR=$WORK/textfile
ABB_OBS_UID=$(id -u)
ABB_OBS_GID=$(id -g)
ABB_RUNBOOK_BASE_URL=https://docs.example.test/abb/
ABB_OBS_RIG_PROM_PORT=$P_PROM
ABB_OBS_RIG_AM_PORT=$P_AM
ABB_OBS_RIG_GRAFANA_PORT=$P_GRAFANA
ENV
cat >"$WORK/edge.env" <<ENV
ABB_ACME_EMAIL=rig@example.invalid
ABB_SITE_ADDRESS=abb.localhost
ABB_EDGE_BIND=127.0.0.1
ABB_EDGE_HTTP_PORT=$P_HTTP
ABB_EDGE_HTTPS_PORT=$P_HTTPS
ABB_EDGE_NETWORK=$NET
ABB_EDGE_SITES_DIR=$WORK/sites
ABB_EDGE_GLOBALS_DIR=$root/deploy/hosting/rig/globals
ENV
mkdir -p "$WORK/sites"; cp deploy/hosting/edge/abb.caddy.template "$WORK/sites/abb.caddy"
dc config -q || fail "the rig compose files do not render"

step "start the edge (real Caddy, local CA), then postgres, migrate, api and worker"
docker network create "$NET" >/dev/null
dc_edge up -d >/dev/null 2>&1 || fail "could not start Caddy"
dc up -d --wait --no-build postgres migrate api worker >"$WORK/up1.log" 2>&1 || { tail -15 "$WORK/up1.log" >&2; fail "the application stack did not start"; }

step "O2 textfiles written by the REAL scripts (backup metrics through the library, container metrics through hosting-container-metrics.sh)"
now="$(date +%s)"
python3 scripts/hosting_backup_lib.py metrics --file "$WORK/textfile/abb_backup.prom" \
  "abb_hosting_backup_last_success_timestamp_seconds=$now" "abb_hosting_backup_offsite_last_success_timestamp_seconds=$now" \
  "abb_hosting_backup_last_run_timestamp_seconds=$now" "abb_hosting_backup_last_run_success=1"
python3 scripts/hosting_backup_lib.py metrics --file "$WORK/textfile/abb_restore_drill.prom" "abb_hosting_restore_drill_last_success_timestamp_seconds=$now"
ABB_BACKUP_ENV=/nonexistent ABB_TEXTFILE_DIR="$WORK/textfile" ABB_METRICS_PROJECTS="$APP $EDGE" scripts/hosting-container-metrics.sh
grep -c '^abb_hosting_container_running' "$WORK/textfile/abb_containers.prom" | sed 's/^/containers reported: /'
grep -q 'abb_hosting_container_running{name="abb-hosting-obs-api-1"} 1' "$WORK/textfile/abb_containers.prom" || fail "the API container is not in the container metrics"
if grep -q 'migrate' "$WORK/textfile/abb_containers.prom"; then fail "the one-shot migrate job must not be reported"; fi

step "O2b a REAL out-of-memory kill is visible to the sampler even after restart: unless-stopped resets State.OOMKilled (review P2-5)"
OOMC="$APP-oomprobe"; docker rm -f "$OOMC" >/dev/null 2>&1 || true
docker run -d --name "$OOMC" --label "com.docker.compose.project=$APP" --memory=16m --memory-swap=16m --restart unless-stopped \
  --entrypoint python "$IMAGE" -c 'import time
time.sleep(2)
b = []
while True: b.append(bytearray(4 * 1024 * 1024))' >/dev/null || fail "could not start the OOM probe"
for _ in $(seq 1 40); do [ "$(docker inspect "$OOMC" --format '{{.RestartCount}}')" -ge 2 ] && break; sleep 1; done
[ "$(docker inspect "$OOMC" --format '{{.RestartCount}}')" -ge 2 ] || fail "the probe was never OOM-killed (Docker did not enforce the limit?)"
echo "inspect now says OOMKilled=$(docker inspect "$OOMC" --format '{{.State.OOMKilled}}') after $(docker inspect "$OOMC" --format '{{.RestartCount}}') restarts"
ABB_BACKUP_ENV=/nonexistent ABB_TEXTFILE_DIR="$WORK/textfile" ABB_METRICS_PROJECTS="$APP" scripts/hosting-container-metrics.sh
grep -E "oom_events\{name=\"$OOMC\"\} [1-9]" "$WORK/textfile/abb_containers.prom" || fail "the sampler did not report the OOM kills from the event log"
docker rm -f "$OOMC" >/dev/null
ABB_BACKUP_ENV=/nonexistent ABB_TEXTFILE_DIR="$WORK/textfile" ABB_METRICS_PROJECTS="$APP $EDGE" scripts/hosting-container-metrics.sh
echo "PASS O2b"

step "start the monitoring profile (obs-init, prometheus, alertmanager, node-exporter, blackbox, grafana) with --wait"
dc up -d --wait --no-build obs-init prometheus alertmanager node-exporter blackbox grafana >"$WORK/up2.log" 2>&1 || { tail -25 "$WORK/up2.log" >&2; fail "the monitoring stack did not become healthy"; }
prom() { curl -fsS --max-time 20 "http://127.0.0.1:$P_PROM$1"; }
am() { curl -fsS --max-time 20 "http://127.0.0.1:$P_AM$1"; }
poll() { # description, timeout seconds, command...  (bounded; no wall-clock assertion beyond the generous deadline)
  local what="$1" limit="$2"; shift 2; local i=0
  until "$@" >/dev/null 2>&1; do i=$((i + 5)); [ "$i" -lt "$limit" ] || fail "timed out waiting for: $what"; sleep 5; done
}
q() { python3 - "$P_PROM" "$1" <<'PY'
import json, sys, urllib.parse, urllib.request
port, query = sys.argv[1:3]
url = f"http://127.0.0.1:{port}/api/v1/query?query=" + urllib.parse.quote(query)
data = json.load(urllib.request.urlopen(url, timeout=20))["data"]["result"]
print(json.dumps(data))
sys.exit(0 if data else 1)
PY
}

step "O3 every target UP (7 jobs), the blackbox probe succeeds against the real Caddy, the rules are loaded and healthy"
poll "all 7 scrape targets up" 240 bash -c "[ \"\$(python3 - $P_PROM <<'PY'
import json, sys, urllib.request
t = json.load(urllib.request.urlopen('http://127.0.0.1:%s/api/v1/targets' % sys.argv[1], timeout=20))['data']['activeTargets']
print(sum(1 for x in t if x['health'] == 'up'))
PY
)\" = 7 ]"
prom /api/v1/targets | python3 -c '
import json, sys
for t in sorted(json.load(sys.stdin)["data"]["activeTargets"], key=lambda x: x["scrapePool"]):
    print("  %-13s %-4s %s" % (t["scrapePool"], t["health"].upper(), t["labels"].get("instance")))'
poll "probe_success{job=abb-https} == 1" 120 q 'probe_success{job="abb-https"} == 1'
q 'probe_ssl_earliest_cert_expiry{job="abb-https"} - time()' | python3 -c 'import json,sys; v=float(json.load(sys.stdin)[0]["value"][1]); print("  certificate seconds left: %.0f" % v); assert v > 0'
rules_ok() { # every alerting rule evaluated at least once (health ok), none in error
  prom /api/v1/rules | python3 -c '
import json, sys
groups = json.load(sys.stdin)["data"]["groups"]
rules = [r for g in groups for r in g["rules"] if r["type"] == "alerting"]
host = {r["name"] for g in groups if g["name"].startswith("abb-host") for r in g["rules"]}
bad = [(r["name"], r["health"]) for r in rules if r["health"] != "ok"]
print("  %d alerting rules loaded, %d host rules, %d not (yet) healthy" % (len(rules), len(host), len(bad)))
assert not any(h == "err" for _, h in bad), bad
sys.exit(0 if not bad and len(host) >= 21 else 1)'
}
poll "every rule evaluated and healthy" 180 rules_ok
rules_ok
echo "PASS O3"

step "O4 host, backup and container series arrive through node_exporter's textfile collector"
for series in 'node_memory_MemAvailable_bytes' 'node_filesystem_avail_bytes{mountpoint="/"}' 'abb_hosting_backup_last_success_timestamp_seconds' \
    'abb_hosting_backup_offsite_last_success_timestamp_seconds' 'abb_hosting_restore_drill_last_success_timestamp_seconds' \
    'abb_hosting_container_restart_count{name="abb-hosting-obs-api-1"}' 'abb_hosting_container_oom_killed{name="abb-hosting-obs-postgres-1"}'; do
  poll "series $series" 120 q "$series"; echo "  present: $series"
done
echo "PASS O4"

step "O5 configuration facts: retention 30 d and 4 GB on a named volume, Alertmanager config checked, runbook links rewritten (KI-245), loopback only"
prom /api/v1/status/flags | python3 -c '
import json, sys
f = json.load(sys.stdin)["data"]
print("  retention.time=%s retention.size=%s" % (f["storage.tsdb.retention.time"], f["storage.tsdb.retention.size"]))
assert f["storage.tsdb.retention.time"] == "30d" and f["storage.tsdb.retention.size"] == "4GiB"'
mount_types="$(docker inspect "$(dc ps -q prometheus)" --format '{{range .Mounts}}{{.Destination}}={{.Type}} {{end}}')"
echo "  prometheus mounts: $mount_types"
case "$mount_types" in *"/prometheus=volume"*) ;; *) fail "the Prometheus data is not on a named volume" ;; esac
checked="$(dc exec -T alertmanager amtool check-config /etc/obs-config/alertmanager/alertmanager.yml 2>&1)" || fail "amtool check-config: $checked"
echo "$checked" | sed 's/^/  /'
case "$checked" in *SUCCESS*) ;; *) fail "amtool check-config did not succeed" ;; esac
# the control: amtool must refuse a broken config (an unknown receiver in the route)
printf 'route:\n  receiver: nobody\nreceivers:\n  - name: other\n' >"$WORK/bad-am.yml"
if docker run --rm --entrypoint amtool -v "$WORK:/t:ro" "$ALERTMANAGER" check-config /t/bad-am.yml >/dev/null 2>&1; then fail "amtool accepted a broken Alertmanager config"; fi
echo "  amtool negative control: a route to an undefined receiver is rejected"

step "O5b receiver from an UNTRACKED file: default = no receiver; email, slack, webhook variants pass amtool; a malformed file is refused before Alertmanager starts"
AMS="$WORK/secrets/alertmanager"
render() { dc run --rm -T --no-deps obs-init >/dev/null 2>&1 || fail "obs-init failed"; }
validate() { dc run --rm -T --no-deps obs-check 2>&1; }
rendered_file() { dc run --rm -T --no-deps --entrypoint cat obs-check /etc/obs-config/alertmanager/alertmanager.yml 2>/dev/null; }
printf 'hunter2-not-a-real-password' >"$AMS/smtp_password"; printf 'https://hooks.example.test/x' >"$AMS/slack_webhook_url"; printf 'https://ntfy.example.test/abb' >"$AMS/webhook_url"
chmod 600 "$AMS"/smtp_password "$AMS"/slack_webhook_url "$AMS"/webhook_url
rm -f "$AMS/notify.yml" "$AMS/deadman.yml"; render
out="$(validate)" || fail "default (no notify.yml) config rejected: $out"
rendered_file >"$WORK/am-default.yml"
grep -q '# __NOTIFY_INTEGRATIONS__' "$WORK/am-default.yml" || fail "the default render lost the marker"
grep -q '^ *email_configs:' "$WORK/am-default.yml" && fail "the default render has an active email receiver"
echo "  default: marker kept, no integration, amtool OK"
variant() { # name, content
  printf '%s\n' "$2" >"$AMS/notify.yml"; chmod 600 "$AMS/notify.yml"; render
  out="$(validate)" || fail "$1: amtool rejected the spliced config: $out"
  rendered_file | grep -q "$3" || fail "$1: the integration is not in the rendered config"
  rendered_file | grep -q '__NOTIFY_INTEGRATIONS__' && fail "$1: the marker was not replaced"
  echo "  $1: spliced and accepted by amtool"
}
variant email 'email_configs:
  - to: owner@example.org
    from: alerts@example.org
    smarthost: smtp.gmail.com:587
    auth_username: alerts@example.org
    auth_password_file: /run/alertmanager-secrets/smtp_password
    headers:
      subject: '"'"'{{ template "abb.title" . }}'"'"'
    text: '"'"'{{ template "abb.text" . }}'"'"'' 'auth_password_file: /run/alertmanager-secrets/smtp_password'
variant slack 'slack_configs:
  - api_url_file: /run/alertmanager-secrets/slack_webhook_url
    channel: "#alerts"' 'api_url_file'
variant webhook 'webhook_configs:
  - url_file: /run/alertmanager-secrets/webhook_url' 'url_file: /run/alertmanager-secrets/webhook_url'
printf 'email_configs:\n  - to: [unclosed\n   bad: : indent\n' >"$AMS/notify.yml"; render
if out="$(validate)"; then fail "a malformed notify.yml passed the pre-start validation"; fi
echo "  malformed notify.yml: refused by obs-check (alertmanager depends on it, so it is not started): $(echo "$out" | tail -1)"
printf 'email_configs:\n  - to: owner@example.org\n    auth_password_file: /run/alertmanager-secrets/missing_file\n' >"$AMS/notify.yml"; render
if out="$(validate)"; then fail "a notify.yml naming a missing secret file passed"; fi
echo "  missing secret file: refused too"
rm -f "$AMS/notify.yml"; render
validate >/dev/null || fail "the default no longer validates after the variants"
echo "PASS O5b"
cat >"$WORK/alert.json" <<'JSON'
{"receiver":"notify","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"HostDiskUsageHigh","severity":"ticket"},
 "annotations":{"summary":"Disk is full","description":"d","runbook_url":"docs/runbooks/hosting-operations.md#disk-is-filling-up"}}],
 "groupLabels":{},"commonLabels":{"alertname":"HostDiskUsageHigh","severity":"ticket"},"commonAnnotations":{},"externalURL":""}
JSON
dc exec -T alertmanager cat /etc/obs-config/alertmanager/abb.tmpl >"$WORK/abb.tmpl"
rendered="$(docker run --rm --entrypoint amtool -v "$WORK:/t:ro" "$ALERTMANAGER" template render --template.glob=/t/abb.tmpl --template.data=/t/alert.json --template.text='{{ template "abb.text" . }}' 2>&1)"
echo "$rendered" | sed 's/^/  render: /'
case "$rendered" in *"Runbook: https://docs.example.test/abb/docs/runbooks/hosting-operations.md#disk-is-filling-up"*) ;; *) fail "the runbook URL was not rewritten" ;; esac
# the control: with no base URL the path stays a path (the template really takes it from ABB_RUNBOOK_BASE_URL, not from a constant)
sed 's#https://docs.example.test/abb/##' "$WORK/abb.tmpl" >"$WORK/abb-empty.tmpl"
empty="$(docker run --rm --entrypoint amtool -v "$WORK:/t:ro" "$ALERTMANAGER" template render --template.glob=/t/abb-empty.tmpl --template.data=/t/alert.json --template.text='{{ template "abb.text" . }}' 2>&1)"
case "$empty" in *"Runbook: docs/runbooks/"*) ;; *) fail "control: an empty base URL should leave the path" ;; esac
pp="$(scripts/hosting-verify.sh --docker-project "$APP")" || fail "published ports: $pp"
echo "$pp" | sed 's/^/  /' | grep 'ports.none_published'

ports="$(docker port "$(dc ps -q grafana)")"; echo "$ports" | sed 's/^/  grafana publishes: /'
case "$ports" in *"0.0.0.0"*|*"[::]"*) fail "grafana is published beyond loopback" ;; esac
echo "PASS O5"

step "O6 Grafana: the password comes from the file (right password in, wrong password refused), never from the environment"
code_ok="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -u "admin:$grafana_pw" "http://127.0.0.1:$P_GRAFANA/api/datasources")"
code_bad="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -u "admin:wrong-$(rand 4)" "http://127.0.0.1:$P_GRAFANA/api/datasources")"
code_anon="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:$P_GRAFANA/api/datasources")"
echo "  file password -> $code_ok, wrong password -> $code_bad, anonymous -> $code_anon"
[ "$code_ok" = 200 ] && [ "$code_bad" = 401 ] && [ "$code_anon" = 401 ] || fail "grafana authentication"
genv="$(dc exec -T grafana env)"
case "$genv" in *"GF_SECURITY_ADMIN_PASSWORD="*) fail "the password is in the environment" ;; esac
ds="$(curl -fsS --max-time 20 -u "admin:$grafana_pw" "http://127.0.0.1:$P_GRAFANA/api/datasources" | python3 -c 'import json,sys; print(",".join(d["name"] for d in json.load(sys.stdin)))')"
echo "  provisioned datasources: $ds"
echo "PASS O6"

step "O7 Watchdog reaches Alertmanager and nothing else is firing on a healthy rig"
poll "Watchdog in Alertmanager" 180 bash -c "curl -fsS http://127.0.0.1:$P_AM/api/v2/alerts | grep -q Watchdog"
am /api/v2/alerts | python3 -c '
import json, sys
names = sorted({a["labels"]["alertname"] for a in json.load(sys.stdin)})
print("  firing in Alertmanager:", names)
assert names == ["Watchdog"], names'
echo "PASS O7"

step "O8 a DELIBERATELY FAILED SCRAPE: stop the worker and node_exporter; AbbTargetDown (page) and AbbHostScrapeDown (ticket) fire and reach Alertmanager"
dc stop worker node-exporter >/dev/null 2>&1
poll "abb-worker and node targets down" 120 q 'up{job=~"abb-worker|node"} == 0'
poll "AbbTargetDown firing in Prometheus" 420 q 'ALERTS{alertname="AbbTargetDown",alertstate="firing",job="abb-worker"}'
poll "AbbHostScrapeDown firing in Prometheus" 420 q 'ALERTS{alertname="AbbHostScrapeDown",alertstate="firing",job="node"}'
poll "both alerts in Alertmanager" 180 bash -c "curl -fsS http://127.0.0.1:$P_AM/api/v2/alerts | python3 -c '
import json, sys
names = {a[\"labels\"][\"alertname\"] for a in json.load(sys.stdin)}
sys.exit(0 if {\"AbbTargetDown\", \"AbbHostScrapeDown\"} <= names else 1)'"
am /api/v2/alerts | python3 -c '
import json, sys
for a in json.load(sys.stdin):
    if a["labels"]["alertname"] in ("AbbTargetDown", "AbbHostScrapeDown"):
        print("  FIRING %-18s severity=%-6s receivers=%s job=%s" % (a["labels"]["alertname"], a["labels"]["severity"], [r["name"] for r in a["receivers"]], a["labels"]["job"]))'
dc start worker node-exporter >/dev/null 2>&1
poll "targets back up" 180 q 'up{job=~"abb-worker|node"} == 1'
poll "alerts resolved in Alertmanager" 300 bash -c "curl -fsS http://127.0.0.1:$P_AM/api/v2/alerts | python3 -c '
import json, sys
names = {a[\"labels\"][\"alertname\"] for a in json.load(sys.stdin)}
sys.exit(0 if not (names & {\"AbbTargetDown\", \"AbbHostScrapeDown\"}) else 1)'"
echo "PASS O8 (fired through the failed scrape, resolved after the restart)"

step "memory seen by Docker (MiB, one sample after the run)"
docker stats --no-stream --format 'MEM {{.Name}} {{.MemUsage}}' $(dc ps -q prometheus alertmanager node-exporter blackbox grafana) 2>/dev/null | sed 's/^/  /'
printf '\nHOSTING-OBS-E2E PASSED\n'
