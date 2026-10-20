"""Gate 1 of scripts/obs-e2e.sh (D9 gate 1): `/metrics` is authenticated, disabled without a token, bounded in cardinality, free of
tenant text, and served by both the API and the worker. Only deterministic facts: status codes, headers, label sets, presence.
"""

import asyncio
import json
import re
from typing import Any

import httpx

import obs_e2e_lib as lib
from obs_e2e_lib import check, gate, info

MIB = 1024 * 1024
# Public ids look like `ws_<26 chars>`: a prefix of 2-4 letters, an underscore and a long run of alphanumerics. No metric name does.
_PUBLIC_ID = re.compile(r"\b[a-z]{2,4}_[0-9A-Za-z]{20,}\b")
_ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")


def _catalogue_names() -> set[str]:
    from abb_api.obs.catalogue import build_observability  # noqa: PLC0415

    return set(build_observability().registry.metrics)


def _auth_and_headers() -> None:
    api = lib.API_URL
    # --- a second API process WITHOUT the token: the endpoint does not exist
    plain = lib.start_server("no-token", lib.API_PORT + 1, {"ABB_METRICS_TOKEN": None})
    try:
        for label, headers in (("no credentials", {}), ("a bearer header", {"Authorization": f"Bearer {lib.metrics_token()}"})):
            reply = httpx.get(f"{plain.url}/metrics", headers=headers, timeout=15)
            unknown = httpx.get(f"{plain.url}/no-such-path", headers=headers, timeout=15)
            check(reply.status_code == 404 and reply.json()["error"]["code"] == unknown.json()["error"]["code"],
                  f"without ABB_METRICS_TOKEN /metrics is 404, the same error code as any unknown path ({label})",
                  status=reply.status_code, code=reply.json().get("error", {}).get("code"))
        spec = httpx.get(f"{plain.url}/openapi.json", timeout=15).json()
        check("/metrics" not in spec["paths"], "without a token /metrics is not in /openapi.json")
    finally:
        plain.stop()

    # --- with the token: wrong and missing credentials get one identical 401
    cases = {
        "no Authorization header": {},
        "a wrong token": {"Authorization": "Bearer " + "0" * 48},
        "the token with one character changed": {"Authorization": "Bearer " + lib.metrics_token()[:-1] + ("0" if lib.metrics_token()[-1] != "0" else "1")},
        "a bearer scheme with no credential": {"Authorization": "Bearer"},
        "a different scheme": {"Authorization": f"Basic {lib.metrics_token()}"},
        "the token without a scheme": {"Authorization": lib.metrics_token()},
    }
    replies = {label: httpx.get(f"{api}/metrics", headers=headers, timeout=15) for label, headers in cases.items()}
    bodies = {label: lib.base.scrub(r.json()) for label, r in replies.items()}
    check(all(r.status_code == 401 for r in replies.values()), "every wrong or missing credential is 401", statuses={k: r.status_code for k, r in replies.items()})
    check(len({json.dumps(b, sort_keys=True) for b in bodies.values()}) == 1, "all of those 401 bodies are identical (no oracle on which part was wrong)",
          code=next(iter(bodies.values()))["error"]["code"])
    check(len({r.headers.get("www-authenticate") for r in replies.values()}) == 1 and next(iter(replies.values())).headers.get("www-authenticate") == "Bearer",
          "all of them carry the same WWW-Authenticate: Bearer")
    check(all(lib.metrics_token() not in r.text for r in replies.values()), "no 401 body echoes the token")

    # A caller-supplied request id is accepted only when short and plain ([A-Za-z0-9._-], at most 64): that is what a log line may carry
    # from a header. Anything else is replaced, so no header text can ride into a log line through it (gate 4 scans for the sentinel).
    plain_id = httpx.get(f"{api}/healthz", headers={"X-Request-ID": "caller-id-123.abc"}, timeout=15).headers.get("x-request-id")
    odd_id = httpx.get(f"{api}/healthz", headers={"X-Request-ID": "has space:and=punctuation"}, timeout=15).headers.get("x-request-id")
    long_id = httpx.get(f"{api}/healthz", headers={"X-Request-ID": "a" * 65}, timeout=15).headers.get("x-request-id")
    check(plain_id == "caller-id-123.abc" and (odd_id or "").startswith("req_") and (long_id or "").startswith("req_"),
          "request ids: a plain caller id (<= 64 of [A-Za-z0-9._-]) is kept for correlation; a spaced, punctuated or over-long one is replaced by a generated req_ id",
          plain=plain_id, replaced_odd=(odd_id or "")[:4], replaced_long=(long_id or "")[:4])
    good = lib.scrape_raw(api)
    check(good.status_code == 200, "the correct token gets 200", status=good.status_code)
    check(good.headers.get("content-type", "").startswith("text/plain; version=0.0.4"), "the content type is the Prometheus text format 0.0.4",
          content_type=good.headers.get("content-type"))
    check(good.headers.get("cache-control") == "no-store", "the response is no-store", cache_control=good.headers.get("cache-control"))
    spec = httpx.get(f"{api}/openapi.json", timeout=15).json()
    check("/metrics" not in spec["paths"], "/metrics is not in /openapi.json even with a token")
    docs = httpx.get(f"{api}/docs", timeout=15)
    check("/metrics" not in docs.text, "/docs does not mention /metrics")
    try:
        families = lib.parse(good.text)
        parsed = True
    except AssertionError as error:
        families, parsed = {}, False
        info(parse_error=str(error)[:200])
    check(parsed and families, "the exposition parses with the minimal 0.0.4 parser of tests/obs", families=len(families), series=lib.series_count(families))
    names = _catalogue_names()
    check(set(families) == names, "the API exposes exactly the catalogue's families", missing=sorted(names - set(families))[:5], extra=sorted(set(families) - names)[:5])
    untyped = [f.name for f in families.values() if not f.type or not f.help]
    check(not untyped, "every family has HELP and TYPE", untyped=untyped[:5])


def _worker() -> None:
    worker = lib.WORKER_URL
    missing = httpx.get(f"{worker}/metrics", timeout=15)
    wrong = httpx.get(f"{worker}/metrics", headers={"Authorization": "Bearer " + "1" * 48}, timeout=15)
    check(missing.status_code == 401 and wrong.status_code == 401 and lib.base.scrub(missing.json()) == lib.base.scrub(wrong.json()),
          "the worker's /metrics: a missing and a wrong token get the same 401", statuses=[missing.status_code, wrong.status_code])
    unknown = httpx.get(f"{worker}/other", headers={"Authorization": f"Bearer {lib.metrics_token()}"}, timeout=15)
    check(unknown.status_code == 404, "the worker serves nothing else (404 for another path)", status=unknown.status_code)
    reply = lib.scrape_raw(worker)
    check(reply.status_code == 200 and reply.headers.get("content-type", "").startswith("text/plain; version=0.0.4"),
          "the worker serves the exposition with the same type", status=reply.status_code)
    check(set(lib.parse(reply.text)) == _catalogue_names(), "the worker exposes the same catalogue families")


def _db_gauges() -> None:
    gauges = ("worker_jobs_pending", "worker_job_lag_seconds", "alert_dead_letters", "alert_destinations", "retention_mode",
              "retention_seconds_since_last_pass", "abb_scrape_database_up")
    for label, url in (("API", lib.API_URL), ("worker", lib.WORKER_URL)):
        up = lib.poll(f"{label}: the scrape-time database sample succeeds (abb_scrape_database_up = 1)",
                      lambda url=url: lib.value(lib.scrape(url), "abb_scrape_database_up") == 1.0, timeout=60)
        families = lib.scrape(url)
        check(up and all(g in families for g in gauges), f"{label} serves the scrape-time database gauges and the sample is fresh", gauges=len(gauges))
        states = {labels["state"] for _, labels, _ in families["alert_destinations"].samples}
        check(states <= {"active", "paused", "failing"}, f"{label}: alert_destinations only uses the three closed states", states=sorted(states))
        modes = {labels["mode"]: v for _, labels, v in families["retention_mode"].samples}
        check(sum(modes.values()) <= 1.0 and set(modes) <= {"off", "dry_run", "enforce"}, f"{label}: retention_mode is one-hot over the closed modes", modes=modes)


def _hostile_flood_and_tenant_text() -> None:
    api = lib.API_URL
    owner = lib.person("owner")
    marks = {kind: lib.sentinel(kind) for kind in ("project", "agent", "path", "header", "query", "attr", "ua")}
    project_name = marks["project"].lower()
    project = lib.new_project(project_name)
    key = lib.new_key(project, ["events:write", "runs:read"], "obs-key")
    lib.remember_secret("api-key", key)
    run = lib.run_ids()
    sent = lib.ingest(api, key, [lib.event(run, 1, agent=marks["agent"].lower()),
                                 {**lib.event(run, 2, agent=marks["agent"].lower()), "attributes": {"tool.name": "shell", "note": marks["attr"]}}],
                      extra_headers={"User-Agent": marks["ua"], "X-Forwarded-For": "203.0.113.77", "X-Request-ID": f"{marks['header']} not:a:valid id", "X-Trace": marks["header"]})
    check(sent.status_code == 202, "a planted batch (sentinels in project, agent, attributes and headers) is accepted", status=sent.status_code)
    # Matched routes with hostile path parameters and unmatched paths carrying the sentinels.
    owner.get(f"/v1/runs/{marks['path']}", params={"q": marks["query"]})
    httpx.get(f"{api}/v1/{marks['path']}/{marks['query']}", headers={"X-Forwarded-For": "203.0.113.78"}, timeout=15)
    httpx.get(f"{api}/v1/runs/{run['run_id']}", headers=lib.bearer(key), timeout=15)

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "PURGE", "BREW", "PROPFIND", "X-CUSTOM"]
    total = 900

    def flood(round_: int) -> list[lib.Reply]:
        async def run() -> list[lib.Reply]:
            async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=24, max_keepalive_connections=0)) as client:
                async def one(i: int) -> lib.Reply:
                    method = methods[i % len(methods)]
                    path = f"/{['v1', 'x', 'a/b', 'v1/runs', 'admin', 'v1/projects'][i % 6]}/{'z' * (1 + i % 400)}-{round_}-{i}/{marks['path']}{i}"
                    headers = {"X-Forwarded-For": f"198.51.100.{i % 250 + 1}", "User-Agent": f"flood-{round_}-{i}-{marks['ua']}"}
                    return await lib.fire(client, method, f"{api}{path}", headers=headers)

                return await lib.run_concurrent(total, 24, one)

        return asyncio.run(run())

    def series(families: dict[str, Any]) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
        return {(name, tuple(sorted(labels.items()))) for fam in families.values() for name, labels, _ in fam.samples}

    before = lib.scrape(api)
    first = flood(1)
    # 404 for an unmatched path or a method the router does not know; 400 from the HTTP parser itself for a verb it rejects (never
    # reaches the application); 401 where an authenticated route matched the prefix.
    allowed = {400, 401, 403, 404, 405, 422, 429}
    lib.only_statuses(first, allowed, "hostile flood: URLs, methods and addresses cause no 5xx and no surprise status")
    middle = lib.scrape(api)
    second = flood(2)
    lib.only_statuses(second, allowed, "hostile flood, a second round of 900 different URLs: no 5xx and no surprise status")
    after = lib.scrape(api)
    new_series = series(middle) - series(before)
    again = series(after) - series(middle)
    routes = {dict(labels).get("route") for _, labels in new_series if "route" in dict(labels)}
    meths = {dict(labels).get("method") for _, labels in new_series if "method" in dict(labels)}
    known_methods = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "other"}
    check(routes <= {"unmatched"} or all(r == "unmatched" or r.startswith("/") and "{" in r or r in {"/healthz", "/readyz"} for r in routes if r),
          "after the flood every new route label is a template or 'unmatched' (never a raw path)", new_routes=sorted(r for r in routes if r)[:8])
    check(meths <= known_methods, "after the flood every new method label is from the closed set (unusual verbs are 'other')", new_methods=sorted(m for m in meths if m))
    check(not again, f"{total} further requests to {total} different URLs created {len(again)} new series: the series set is bounded by method x status, not by URL",
          first_round_new=len(new_series), second_round_new=len(again), sample=sorted(map(str, again))[:3])
    unmatched = lib.total(after, "http_requests_total", route="unmatched") - lib.total(before, "http_requests_total", route="unmatched")
    reached = sum(1 for r in first + second if r.status in {401, 403, 404, 405, 422, 429})
    check(unmatched <= reached and unmatched > 0, "the flood is counted under the one 'unmatched' route", unmatched=unmatched, answered_by_the_app=reached)
    text = lib.scrape_raw(api).text
    leaked = [m for m in marks.values() if m in text] + [m.lower() for m in marks.values() if m.lower() in text.lower()]
    check(not leaked, "no sentinel (project, agent, path, query, attribute, header, user agent) appears in the API exposition", leaked=leaked[:3])
    worker_text = lib.scrape_raw(lib.WORKER_URL).text
    for label, body in (("API", text), ("worker", worker_text)):
        found = {
            "public ids": _PUBLIC_ID.findall(body)[:3], "addresses": _ADDRESS.findall(body)[:3], "e-mail": _EMAIL.findall(body)[:3],
            "api key": [key.split(".")[0]] if key.split(".")[0] in body else [], "workspace slug": [lib.base.SLUG] if lib.base.SLUG in body else [],
            "session cookie": [t for t in lib.base.state()["sessions"].values() if t in body], "metrics token": [lib.metrics_token()] if lib.metrics_token() in body else [],
        }
        check(not any(found.values()), f"{label} exposition: no tenant id, slug, key, session, e-mail address or client address",
              found={k: len(v) for k, v in found.items() if v})
    labels_seen = {k for fam in after.values() for _, labels, _ in fam.samples for k in labels}
    allowed = {"method", "route", "status", "le", "version", "outcome", "reason", "type", "scope", "kind", "quota", "mode", "tier", "code", "state", "check", "status",
               "source"}
    check(labels_seen <= allowed, "the label names in the exposition are a short closed vocabulary", extra=sorted(labels_seen - allowed))


def gate1() -> None:
    gate("gate1-endpoint")
    _auth_and_headers()
    _worker()
    _db_gauges()
    _hostile_flood_and_tenant_text()
    lib.finish()
