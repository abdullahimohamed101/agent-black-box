"""Helper of scripts/demo.sh: publish the demo policy to the demo project, as the demo owner. Run with `uv run --project apps/api python`.

    demo_policy.py publish POLICY_FILE   publishes the file as the project's policy unless the project already has this exact document
    demo_policy.py run-of APPROVAL_ID    prints the id of the run that asked for this approval

Environment: DEMO_API, DEMO_WEB (the Origin header a browser session sends), DEMO_SESSION (a development session of the demo
owner; never printed), DEMO_WORKSPACE, DEMO_PROJECT. It touches only the demo project's policy.
"""

import os
import sys
from pathlib import Path

import httpx


def person() -> httpx.Client:
    client = httpx.Client(
        base_url=os.environ["DEMO_API"],
        cookies={"abb_session": os.environ["DEMO_SESSION"]},
        headers={"Origin": os.environ["DEMO_WEB"]},
        timeout=30,
    )
    me = client.get("/v1/me")
    me.raise_for_status()
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == os.environ["DEMO_WORKSPACE"]:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            return client
    raise SystemExit("the demo owner is not a member of the demo workspace")


def publish(path: str) -> None:
    http = person()
    projects = http.get("/v1/projects").raise_for_status().json()["items"]
    project = next(
        (p for p in projects if p["slug"] == os.environ["DEMO_PROJECT"]), None
    )
    if project is None:
        raise SystemExit("the demo project does not exist")
    document = Path(path).read_text()
    valid = http.post("/v1/policies/validate", json={"document": document}).json()
    if not valid.get("valid"):
        raise SystemExit(f"the demo policy is not valid: {valid}")
    response = http.post(
        "/v1/policies/publish",
        json={
            "document": document,
            "comment": "demo policy (make demo)",
            "project_id": project["id"],
        },
    )
    if response.status_code not in (200, 201):
        raise SystemExit(
            f"publish failed: {response.status_code} {response.text[:200]}"
        )
    print(f"published demo policy version {response.json().get('version')}")


def run_of(approval_id: str) -> None:
    http = person()
    body = (
        http.get("/v1/approvals", params={"state": "pending", "limit": 100})
        .raise_for_status()
        .json()
    )
    found = next((a for a in body["items"] if a["id"] == approval_id), None)
    if found is None:
        raise SystemExit("that approval is not pending")
    print(found["run_id"])


if __name__ == "__main__":
    commands = {
        "publish": lambda: publish(sys.argv[2]),
        "run-of": lambda: run_of(sys.argv[2]),
    }
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        raise SystemExit(
            "usage: demo_policy.py publish POLICY_FILE | run-of APPROVAL_ID"
        )
    commands[sys.argv[1]]()
