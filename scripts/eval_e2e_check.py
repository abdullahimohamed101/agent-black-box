"""API-side steps of scripts/eval-e2e.sh (Phase 11). Run with `uv run --project apps/api python`.

    setup     wait for both demo runs to be summarised, create the evaluators, evaluate both runs (rule, metric and the
              fake judge), prove that a deliberately failing evaluator leaves the run's row byte-identical, create the
              experiment; print one JSON object of ids for the Playwright spec
    verify    API-level assertions that need no browser: the viewer's comparison is a metadata projection with no
              free text, the owner's is content, and the viewer's evaluations carry no explanation

Environment: E2E_API, E2E_WEB_ORIGIN, E2E_WORKSPACE (slug), E2E_OWNER_SESSION, E2E_VIEWER_SESSION, E2E_BASELINE_RUN,
E2E_WRONG_RUN. Nothing here prints a credential, an explanation or event content.
"""

import json
import os
import sys
import time
from typing import Any

import httpx

API = os.environ.get("E2E_API", "http://localhost:8180")
ORIGIN = os.environ.get("E2E_WEB_ORIGIN", "http://localhost:3180")
BASELINE = os.environ.get("E2E_BASELINE_RUN", "")
WRONG = os.environ.get("E2E_WRONG_RUN", "")
HOSTILE = '<img src=x onerror="alert(1)">'


def fail(message: str) -> None:
    print(f"EVAL-E2E CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def client(kind: str) -> httpx.Client:
    token = os.environ["E2E_OWNER_SESSION" if kind == "owner" else "E2E_VIEWER_SESSION"]
    c = httpx.Client(
        base_url=API, cookies={"abb_session": token}, headers={"origin": ORIGIN}, timeout=30
    )
    me = c.get("/v1/me")
    me.raise_for_status()
    for m in me.json()["memberships"]:
        if m["workspace"]["slug"] == os.environ["E2E_WORKSPACE"]:
            c.headers["x-abb-workspace"] = m["workspace"]["id"]
            return c
    fail(f"{kind} is not a member of the workspace")
    raise AssertionError


def ok(r: httpx.Response, what: str) -> Any:
    if r.status_code >= 300:
        fail(f"{what}: HTTP {r.status_code} {r.json().get('error', {}).get('code')}")
    return r.json() if r.content else None


def wait(what: str, check: Any, seconds: float = 90) -> Any:
    end = time.time() + seconds
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    fail(f"timed out waiting for {what}")


def run_row(c: httpx.Client, run_id: str) -> dict[str, Any]:
    return ok(c.get(f"/v1/runs/{run_id}"), "get run")


def settled(c: httpx.Client, run_id: str) -> dict[str, Any] | None:
    r = run_row(c, run_id)
    done = r["summary_state"] == "current" and r["status"] in ("SUCCESS", "FAILED")
    return r if done and r["findings"] is not None else None


def evaluator(c: httpx.Client, name: str, kind: str, implementation: str, **config: Any) -> str:
    body = {"name": name, "kind": kind, "implementation": implementation, "config": config}
    return ok(c.post("/v1/evaluators", json=body), f"create {name}")["id"]


def evaluations(c: httpx.Client, run_id: str) -> list[dict[str, Any]]:
    return ok(c.get(f"/v1/runs/{run_id}/evaluations"), "list evaluations")["items"]


def terminal(c: httpx.Client, run_id: str, count: int) -> list[dict[str, Any]] | None:
    rows = [e for e in evaluations(c, run_id) if e["latest"]]
    done = [e for e in rows if e["state"] in ("completed", "failed")]
    return done if len(done) >= count and len(done) == len(rows) else None


def setup() -> None:
    owner = client("owner")
    for run_id in (BASELINE, WRONG):
        wait(f"run {run_id} to settle", lambda r=run_id: settled(owner, r))
    base_status = run_row(owner, BASELINE)["status"]
    wrong_status = run_row(owner, WRONG)["status"]
    if (base_status, wrong_status) != ("SUCCESS", "FAILED"):
        fail(f"expected SUCCESS and FAILED, got {base_status} and {wrong_status}")

    ids = {
        "tests": evaluator(owner, "tests-pass", "deterministic", "tests_passed"),
        "cost": evaluator(owner, "cost-under", "metric", "cost_under", max_usd=5),
        "reviewer": evaluator(owner, "reviewer", "human", "human", allowed_labels=["good", "bad"]),
        "judge": evaluator(
            owner, "quality-judge", "llm_judge", "judge", rubric="Did the run fix the bug?"
        ),
        "succeeded": evaluator(owner, "run-succeeded", "deterministic", "run_succeeded"),
        "latency": evaluator(owner, "latency-under", "metric", "latency_under", max_ms=600000),
    }
    for run_id in (BASELINE, WRONG):
        for key in ("tests", "cost", "judge"):
            ok(
                owner.post(f"/v1/runs/{run_id}/evaluations", json={"evaluator_id": ids[key]}),
                f"trigger {key}",
            )
    for run_id in (BASELINE, WRONG):
        rows = wait(f"evaluations of {run_id}", lambda r=run_id: terminal(owner, r, 3))
        bad = [e["evaluator_name"] for e in rows if e["state"] != "completed"]
        if bad:
            fail(f"evaluators failed on a healthy run: {bad}")
    # a human verdict on the baseline carrying hostile text (shown as text to the owner, withheld from a viewer)
    ok(
        owner.post(
            f"/v1/runs/{BASELINE}/verdicts",
            json={
                "evaluator_id": ids["reviewer"],
                "label": "good",
                "score": 1,
                "explanation": HOSTILE,
            },
        ),
        "verdict",
    )

    # an evaluator that fails never changes the run: the rubric leaves no room for the run data, so the row is `failed`
    ids["broken"] = evaluator(
        owner,
        "broken-judge",
        "llm_judge",
        "judge",
        rubric='"' * 4000,
        max_input_chars=5000,
    )
    before = {k: v for k, v in run_row(owner, WRONG).items() if k not in ("findings", "matched")}
    ok(owner.post(f"/v1/runs/{WRONG}/evaluations", json={"evaluator_id": ids["broken"]}), "trigger")
    broken = wait(
        "the failing evaluator",
        lambda: next(
            (
                e
                for e in evaluations(owner, WRONG)
                if e["evaluator_id"] == ids["broken"] and e["state"] in ("completed", "failed")
            ),
            None,
        ),
    )
    if broken["state"] != "failed":
        fail("the deliberately failing evaluator did not fail")
    time.sleep(3)  # a (wrongly) enqueued summary would land within the debounce
    after = {k: v for k, v in run_row(owner, WRONG).items() if k not in ("findings", "matched")}
    if before != after:
        diff = sorted(k for k in before if before[k] != after.get(k))
        fail(f"a failed evaluation changed the run row: {diff}")

    project_id = run_row(owner, WRONG)["project_id"]
    exp = ok(
        owner.post(
            "/v1/experiments",
            json={
                "name": "<b>Fix strategy</b> experiment",
                "project_id": project_id,
                "dataset_label": "oauth-session-expiry",
                "evaluator_ids": [ids["succeeded"], ids["latency"]],
            },
        ),
        "create experiment",
    )
    variants = {}
    for name, role, model, variant in (
        ("baseline", "baseline", "scripted-agent-1", "baseline"),
        ("wrong-fix", "candidate", "scripted-agent-2", "wrong-fix"),
    ):
        variants[name] = ok(
            owner.post(
                f"/v1/experiments/{exp['id']}/variants",
                json={"name": name, "role": role, "config": {"model": model, "variant": variant}},
            ),
            f"variant {name}",
        )["id"]
    for run_id, name in ((BASELINE, "baseline"), (WRONG, "wrong-fix")):
        ok(
            owner.post(
                f"/v1/experiments/{exp['id']}/runs",
                json={"run_id": run_id, "variant_id": variants[name]},
            ),
            "attach run",
        )
    ids.update({"experiment": exp["id"], "baseline_run": BASELINE, "wrong_run": WRONG})
    print(json.dumps(ids))


def verify() -> None:
    owner, viewer = client("owner"), client("viewer")
    path = f"/v1/runs/{BASELINE}/compare"
    full = ok(owner.get(path, params={"with_run_id": WRONG}), "owner compare")
    meta = ok(viewer.get(path, params={"with_run_id": WRONG}), "viewer compare")
    if (full["alignment"], meta["alignment"]) != ("content", "metadata"):
        fail("alignment modes are wrong")
    if not full["files"]["only_right"] or meta["files"]["paths_withheld"] is not True:
        fail("file sets are wrong")
    if any(
        r[side] and r[side]["label"] and ("/" in r[side]["label"] or " " in r[side]["label"])
        for r in meta["steps"]
        for side in ("left", "right")
    ):
        fail("a viewer's step label carries content")
    if full["divergence"]["index"] == meta["divergence"]["index"]:
        fail("expected the divergence to differ by projection")
    if meta["divergence"]["index"] != meta["first_outcome_difference"]:
        fail("the metadata divergence is not the first outcome difference")
    texts = [e for e in evaluations(viewer, BASELINE)]
    # built-in explanations are numbers from a template; only a person's and a judge's free text is withheld
    free = [e for e in texts if e["kind"] in ("human", "llm_judge")]
    if not free or any(e["explanation"] is not None or not e["explanation_withheld"] for e in free):
        fail("a viewer received a human or judge explanation")
    if HOSTILE in json.dumps(texts):
        fail("a viewer received an explanation")
    if not any(e["label"] for e in texts):
        fail("a viewer lost the labels")
    print("verify ok")


if __name__ == "__main__":
    {"setup": setup, "verify": verify}[sys.argv[1]]()
