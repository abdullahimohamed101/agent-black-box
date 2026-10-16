"""Gate 4 and the summary of scripts/obs-e2e.sh (D9 gate 4): the logs of EVERY process the run started (all at DEBUG), scanned for what must
never be there, and the pass/fail table of the whole run.

Per log line: valid JSON with the required fields (uvicorn's own lifecycle lines are the only plain text allowed), the request line
carries `route` (a template or `unmatched`) and no raw `path`/`query`/`url`, no field that could hold a header, body, cookie, address
or payload, and no planted sentinel, API key, session cookie, metrics token, database password or DSN, e-mail address or client
address from anywhere in the run. Then: no Traceback and no ERROR line except the reviewed, counted ones below. Processes that were
broken ON PURPOSE write `expected-*.log`; those are scanned for secrets too, and are not held to the no-ERROR rule.
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

import obs_e2e_lib as lib
from obs_e2e_lib import check, gate, info

REQUIRED = ("timestamp", "level", "service", "logger", "message", "request_id")
SERVICES = {"abb-api", "abb-worker", "abb-retention"}
# uvicorn prints these itself (not through our JSON formatter); nothing it prints carries request data (its access log is off).
PLAIN_ALLOWED = re.compile(
    r"^(INFO|WARNING):\s+(Invalid HTTP request received\.|Started server process \[\d+\]|Waiting for application (startup|shutdown)\.|Application (startup|shutdown) complete\.|"
    r"Uvicorn running on http://(127\.0\.0\.1|0\.0\.0\.0):\d+ \(Press CTRL\+C to quit\)|Shutting down|Finished server process \[\d+\])$"
)
# Keys a log line must never carry: anything that could hold a header, body, cookie, query, URL or client address.
FORBIDDEN_KEYS = {"path", "query", "url", "headers", "header", "cookie", "cookies", "authorization", "body", "payload", "client", "client_ip", "remote_addr", "remote_address",
                  "address", "ip", "email", "token", "password", "secret", "user_agent", "referer", "origin"}
# Reviewed ERROR lines: (message, most times it may appear in the whole run, why).
ERRORS_ALLOWED: list[tuple[str, int, str]] = [
    ("job has no handler", 1, "gate 2 plants exactly one job type with no handler to prove the dead-letter metrics"),
    ("job failed", 1, "the same job is reported once by the worker when it is dead-lettered (outcome dead_letter)"),
    ("alert delivery failed", 2, "gate 2: the relay is a closed port, two attempts (ABB_ALERTS_MAX_ATTEMPTS=2)"),
    ("alert notification dead", 1, "gate 2: the second attempt dead-letters the notification"),
    ("database role became too privileged; /readyz now fails", 1, "gate 3 grants UPDATE on events to the production process on purpose, once"),
]
ADDRESS = re.compile(r"\b(203\.0\.113\.\d+|198\.51\.100\.\d+|192\.0\.2\.\d+)\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")
DSN = re.compile(r"postgres(?:ql)?(?:\+\w+)?://\S*@", re.I)
_PUBLIC_ID_IN_ROUTE = re.compile(r"/(ws|prj|run|evt|key)_[0-9A-Za-z]{10,}")


def _logs() -> list[Path]:
    return sorted(p for p in lib.WORK.glob("*.log") if p.name.startswith(("api-", "api_", "worker", "expected-")))


def _needles() -> dict[str, str]:
    state = lib.base.state()
    needles: dict[str, str] = {}
    for label, mark in state.get("sentinels", {}).items():
        needles[f"sentinel:{label}"] = mark
    for label, secret in state.get("secrets", {}).items():
        if secret:
            needles[f"secret:{label}"] = secret
            if label.startswith("api-key"):
                needles[f"key-secret-part:{label}"] = secret.split(".", 1)[-1]  # the secret half of abb_live_<id>.<secret>
    for role, address in state.get("people", {}).items():
        needles[f"email:{role}"] = address
    needles["workspace-slug"] = lib.base.SLUG
    return {k: v for k, v in needles.items() if len(v) >= 8}


# Catalogue families this run does not move, each with the reason and the test that does. A family added to the catalogue that is neither
# driven here nor listed here fails the run, so "every catalogue metric moves" cannot silently stop being true.
UNEXERCISED_OK: dict[str, str] = {
    "body_bytes_in_flight": "an instantaneous gauge, non-zero only while large bodies overlap; tests/obs/test_control_metrics.py, and hardening-e2e gate 4 drives the real flood",
    "body_cap_refusals_total": "needs a flood of concurrent large bodies against a small byte budget; tests/obs/test_control_metrics.py and hardening-e2e gate 4 (KI-261)",
    "invitation_only_refusals_total": "needs a browser-less OIDC sign-in by an uninvited person (the fake provider); tests/obs/test_control_metrics.py and hardening-e2e gate 5 (KI-261)",
    "limiter_fail_open_total": "needs the database to fail inside the shared limiter while authentication still works; tests/obs/test_control_metrics.py (KI-261)",
    "retention_failures_total": "needs a failing retention step or an unreadable inventory; tests/retention/test_metrics.py (KI-261)",
    "search_busy_total": "needs search fairness exhausted (permits per actor); tests/obs/test_control_metrics.py and hardening-e2e gate 6 (KI-261)",
    "search_permits_in_use": "an instantaneous gauge, non-zero only while a search runs; tests/obs/test_control_metrics.py (KI-261)",
    "sse_connections": "needs an open live-stream consumer; tests/obs/test_platform_metrics.py and stream-e2e.sh (KI-261)",
    "sse_delivery_lag_seconds": "needs events delivered to an open live stream; tests/obs/test_platform_metrics.py and stream-e2e.sh (KI-261)",
}


def _coverage() -> None:
    """Which catalogue families showed a non-zero sample in any exposition or textfile of this run."""
    gate("coverage")
    from abb_api.obs.catalogue import build_observability  # noqa: PLC0415

    for url in (lib.API_URL, lib.WORKER_URL):
        try:
            lib.scrape(url)  # the main stack's final state counts too
        except (SystemExit, httpx.HTTPError):  # the main worker is stopped by the tracing gate
            pass
    names = set(build_observability().registry.metrics)
    moved_path = lib.WORK / "moved.json"
    moved = set(json.loads(moved_path.read_text())) if moved_path.exists() else set()
    unexercised = sorted(names - moved)
    info(catalogue_families=len(names), moved_in_this_run=len(names & moved), not_moved=unexercised)
    unknown = [n for n in unexercised if n not in UNEXERCISED_OK]
    stale = [n for n in UNEXERCISED_OK if n in moved]
    check(not unknown, f"{len(names & moved)} of {len(names)} catalogue families moved in this run; every other one is listed with its reason", not_driven_and_not_listed=unknown)
    check(not stale, "the list of families this run does not move has no stale entry (a listed family that now moves must be removed)", stale=stale)


def summary() -> None:
    gate("gate4-logs")
    needles = _needles()
    logs = _logs()
    check(len(logs) >= 2, "every process of the run wrote a log that is scanned", logs=[p.name for p in logs])
    totals: Counter[str] = Counter()
    problems: dict[str, list[str]] = {}
    errors: Counter[str] = Counter()
    levels: Counter[str] = Counter()
    request_lines = 0
    operations: set[str] = set()

    def flag(kind: str, log: Path, detail: str) -> None:
        problems.setdefault(kind, []).append(f"{log.name}: {detail}"[:200])

    for log in logs:
        expected = log.name.startswith("expected-")
        for number, raw in enumerate(log.read_text(errors="replace").splitlines(), 1):
            line = raw.rstrip()
            if not line:
                continue
            totals["lines"] += 1
            lowered = line.lower()
            for label, needle in needles.items():
                if needle in line or needle.lower() in lowered:
                    flag("secret-or-sentinel", log, f"line {number} contains {label}")
            if "abb_live_" in line:
                flag("secret-or-sentinel", log, f"line {number} contains an API key")
            if DSN.search(line):
                flag("dsn", log, f"line {number} contains a database URL with credentials")
            if ADDRESS.search(line):
                flag("address", log, f"line {number} contains a planted client address")
            for found in EMAIL.findall(line):
                flag("email", log, f"line {number} contains an e-mail address ({found.split('@')[1]})")
            try:
                record: Any = json.loads(line)
            except ValueError:
                if PLAIN_ALLOWED.match(line):
                    totals["uvicorn lifecycle"] += 1
                else:
                    flag("not-json", log, f"line {number}: {line[:60]}")
                continue
            if not isinstance(record, dict):
                flag("not-json", log, f"line {number} is JSON but not an object")
                continue
            missing = [f for f in REQUIRED if f not in record]
            if missing:
                flag("required-fields", log, f"line {number} lacks {missing}")
            if record.get("service") not in SERVICES:
                flag("required-fields", log, f"line {number} has service {record.get('service')!r}")
            bad_keys = FORBIDDEN_KEYS & {str(k).lower() for k in record}
            if bad_keys:
                flag("forbidden-fields", log, f"line {number} has {sorted(bad_keys)}")
            levels[str(record.get("level"))] += 1
            if "operation" in record:
                operations.add(str(record["operation"]).split(".")[0])
            if "status" in record and "duration_ms" in record and "method" in record:  # the request line
                request_lines += 1
                route = record.get("route")
                if not isinstance(route, str) or not (route == "unmatched" or route.startswith("/")):
                    flag("request-line", log, f"line {number}: route={route!r}")
                elif any(ch in route for ch in "?#") or _PUBLIC_ID_IN_ROUTE.search(route):
                    flag("request-line", log, f"line {number}: route is not a template")
            if str(record.get("level")) in {"ERROR", "CRITICAL"} and not expected:
                errors[str(record.get("message"))] += 1
            if "Traceback" in line and not expected:
                flag("traceback", log, f"line {number}")
    info(lines=totals["lines"], uvicorn_lifecycle=totals["uvicorn lifecycle"], levels=dict(levels), request_lines=request_lines, operations=sorted(operations))
    check(totals["lines"] > 0 and levels["DEBUG"] > 0, "the processes ran at DEBUG (DEBUG lines are present in the logs scanned)", debug_lines=levels["DEBUG"], lines=totals["lines"])
    check(not problems.get("not-json"), "every line is JSON (uvicorn's own lifecycle lines excepted)", offenders=problems.get("not-json", [])[:3])
    check(not problems.get("required-fields"), "every JSON line has timestamp, level, service, logger, message and request_id", offenders=problems.get("required-fields", [])[:3])
    check(not problems.get("forbidden-fields"), "no line carries a field that could hold a header, body, cookie, query, URL, address or credential", offenders=problems.get("forbidden-fields", [])[:3])
    check(request_lines > 0 and not problems.get("request-line"), "the request line carries `route` (a template or `unmatched`) and no raw path", request_lines=request_lines,
          offenders=problems.get("request-line", [])[:3])
    check(not problems.get("secret-or-sentinel"), f"no planted sentinel, API key, session cookie, metrics token or database password in any log ({len(needles)} needles, all at DEBUG)",
          offenders=problems.get("secret-or-sentinel", [])[:3])
    check(not problems.get("dsn") and not problems.get("address") and not problems.get("email"), "no database URL, client address or e-mail address in any log",
          offenders={k: problems.get(k, [])[:2] for k in ("dsn", "address", "email") if problems.get(k)})
    unexpected: dict[str, int] = {}
    for message, count in errors.items():
        allowed = next((limit for text, limit, _ in ERRORS_ALLOWED if text in message), 0)
        if count > allowed:
            unexpected[message] = count
    check(not unexpected and not problems.get("traceback"), "no Traceback and no ERROR line outside the reviewed list", errors=dict(errors), unexpected=unexpected,
          tracebacks=problems.get("traceback", [])[:3])

    _coverage()
    rows = [json.loads(line) for line in lib.base.RESULTS.read_text().splitlines() if line.strip()]
    by_gate: dict[str, list[bool]] = {}
    for row in rows:
        by_gate.setdefault(row["gate"], []).append(row["ok"])
    print("\n  gate                      checks  passed  failed")
    for name, oks in by_gate.items():
        print(f"  {name:<24} {len(oks):>7}  {sum(oks):>6}  {len(oks) - sum(oks):>6}")
    failures = [r for r in rows if not r["ok"]]
    print(f"\n  TOTAL {len(rows)} checks, {len(rows) - len(failures)} passed, {len(failures)} failed")
    for row in failures:
        print(f"  FAIL [{row['gate']}] {row['label']}")
    lib.finish()
    if failures:
        raise SystemExit(1)
