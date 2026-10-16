"""One authorization case per route: the action it needs and how to call it validly.

Adding a route without adding a case here fails `test_registry_is_complete`. A case knows how to
build a valid request against the seeded fixtures of either tenant, and which of its parameters
are tenant-owned ids (so the cross-workspace test can replay them with foreign and random ids).
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from abb_event_schema.ids import IdKind, new_id

from abb_api.authz import actions
from tests.ingest_helpers import make_run_ids, wire_event

# A route that serves no tenant data and needs no credential. Literal on purpose: widening it is a
# reviewed change, never a side effect of adding a route.
PUBLIC: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/docs"),
        ("HEAD", "/docs"),
        ("GET", "/openapi.json"),
        ("HEAD", "/openapi.json"),
        ("GET", "/redoc"),
        ("HEAD", "/redoc"),
        ("GET", "/docs/oauth2-redirect"),
        ("HEAD", "/docs/oauth2-redirect"),
        # Sign-in: unauthenticated by nature (they create the session) or credential-optional
        # (logout of an already dead session is still a success). Their own tests cover them.
        ("GET", "/v1/auth/login"),
        ("GET", "/v1/auth/callback"),
        ("POST", "/v1/auth/logout"),
    }
)

# Operator routes: no tenant data, no workspace, no session, no API key. They carry their own
# bearer token, exist only when configured, and stay out of the OpenAPI document (ADR-165).
OPERATOR_ONLY: frozenset[tuple[str, str]] = frozenset(
    {
        # Only when ABB_METRICS_TOKEN is set; 404 otherwise.
        ("GET", "/metrics"),
    }
)

# Routes that need a signed-in person but no particular workspace and no action.
SESSION_ONLY: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/v1/me"),
        ("POST", "/v1/invitations/accept"),
        ("GET", "/v1/auth/sessions"),
        ("POST", "/v1/auth/sessions/revoke-others"),
        ("DELETE", "/v1/auth/sessions/{session_id}"),
    }
)


@dataclass(frozen=True)
class Side:
    """The seeded ids of one tenant, as public ids."""

    name: str
    workspace_id: str
    project_id: str
    run_id: str
    event_id: str
    artifact_id: str
    user_id: str  # another member of the workspace (not a role actor)
    invitation_id: str
    key_id: str  # an API key of the workspace (made by the CLI: no creator)
    # Phase 11: a path-bearing evaluator, a model-judge evaluator with a completed evaluation
    # (its explanation carries a canary), an experiment with a baseline variant holding a run, and
    # a second run of the tenant (the `?with=` of a comparison; a Side field because the id
    # parameter's name must be an attribute of the Side).
    evaluator_id: str
    evaluation_id: str
    experiment_id: str
    variant_id: str
    with_run_id: str
    human_evaluator_id: str  # a `human` evaluator (the target of a verdict)
    approval_id: str  # a waiting approval requested by the tenant's project key (Phase 14)
    agent_slug: str  # the agent the tenant's seeded run belongs to
    destination_id: str  # an alert destination of the tenant (Phase 17)
    rule_id: str  # an alert rule of the tenant (Phase 17)
    every_id: frozenset[str]  # every id the tenant owns (for leak scans)


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # "member" | "invitation" | "api_key" | "evaluator": the world creates a fresh target in acme
    # and puts its id where the path says FRESH, so a mutating request is valid for every actor
    # that may send it.
    fresh: str | None = None


Builder = Callable[[Side, dict[str, str]], RequestSpec]


@dataclass(frozen=True)
class RouteCase:
    method: str
    template: str
    action: str
    build: Builder
    success: int
    id_params: tuple[str, ...] = ()
    transport: Literal["asgi", "socket"] = "asgi"
    # Who replays foreign/random ids for this route (None: the five read actors). Routes that keys
    # never reach list only people.
    probe_actors: tuple[str, ...] | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.template)


def _ids(side: Side, overrides: dict[str, str]) -> dict[str, str]:
    return {
        "run_id": side.run_id,
        "event_id": side.event_id,
        "artifact_id": side.artifact_id,
        "evaluator_id": side.evaluator_id,
        "evaluation_id": side.evaluation_id,
        "experiment_id": side.experiment_id,
        "variant_id": side.variant_id,
        "with_run_id": side.with_run_id,
        "human_evaluator_id": side.human_evaluator_id,
        "approval_id": side.approval_id,
        "destination_id": side.destination_id,
        "rule_id": side.rule_id,
        "workspace_id": side.workspace_id,
        **overrides,
    }


def _get(template: str) -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        ids = _ids(side, overrides)
        params = {"project_id": overrides["project_id"]} if "project_id" in overrides else {}
        return RequestSpec("GET", template.format(**ids), params)

    return build


def _post_event(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = json.dumps(wire_event(make_run_ids(), 1)).encode()
    return RequestSpec(
        "POST", "/v1/events", content=body, headers={"content-type": "application/json"}
    )


def _post_batch(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = json.dumps({"events": [wire_event(make_run_ids(), 1)]}).encode()
    return RequestSpec(
        "POST", "/v1/events/batch", content=body, headers={"content-type": "application/json"}
    )


def _post_run(side: Side, overrides: dict[str, str]) -> RequestSpec:
    return RequestSpec(
        "POST", "/v1/runs", content=b"{}", headers={"content-type": "application/json"}
    )


def _put_artifact(side: Side, overrides: dict[str, str]) -> RequestSpec:
    artifact_id = new_id(IdKind.ARTIFACT)  # a fresh id each time: uploads are idempotent per id
    return RequestSpec(
        "PUT",
        f"/v1/artifacts/{artifact_id}",
        params={"run_id": side.run_id, "kind": "stdout"},
        content=b"matrix probe",
        headers={"content-type": "text/plain"},
    )


def _list_projects(side: Side, overrides: dict[str, str]) -> RequestSpec:
    return RequestSpec("GET", "/v1/projects")


def _create_project(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = json.dumps({"name": "Probe", "slug": f"probe-{uuid.uuid4().hex[:10]}"}).encode()
    return RequestSpec(
        "POST", "/v1/projects", content=body, headers={"content-type": "application/json"}
    )


JSON = {"content-type": "application/json"}


def _security_settings(method: str) -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        project = overrides.get("project_id", side.project_id)
        content = json.dumps({"muted_detectors": ["sec.risky_git"]}).encode()
        return RequestSpec(
            method, f"/v1/projects/{project}/security-settings",
            content=content if method == "PUT" else None,
            headers=JSON if method == "PUT" else {},
        )  # fmt: skip

    return build


def _quotas(method: str) -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        project = overrides.get("project_id", side.project_id)
        content = json.dumps({"events_per_day": 4_000_000}).encode()
        return RequestSpec(
            method, f"/v1/projects/{project}/quotas",
            content=content if method == "PUT" else None,
            headers=JSON if method == "PUT" else {},
        )  # fmt: skip

    return build


def _retention(method: str, suffix: str = "") -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        workspace = overrides.get("workspace_id", side.workspace_id)
        return RequestSpec(
            method, f"/v1/workspaces/{workspace}/retention{suffix}",
            content=json.dumps({"audit": 400}).encode() if method == "PUT" else None,
            headers=JSON if method == "PUT" else {},
        )  # fmt: skip

    return build


POLICY_DOC = (
    "version: 1\npolicies:\n  - id: deny-env\n    description: Do not read dotenv files.\n"
    "    when: {capability: filesystem.read, resource: '**/.env*'}\n    decision: deny\n"
    # The gate the approval routes' cases ask about: a publish by the policy cases must not remove
    # it (it needs a developer, so owner, admin, security and developer may all decide it).
    "  - id: matrix-gate\n    description: Gated for the matrix.\n"
    "    when: {capability: matrix.gate}\n    decision: require_approval\n"
    "    approver_role: developer\n    expires_in: 1h\n"
)


def _policy_get(template: str, extra: dict[str, str] | None = None) -> Builder:
    """GET on the policy routes: the workspace base unless the probe names a project."""

    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        params = {**(extra or {})}
        if "project_id" in overrides:
            params["project_id"] = overrides["project_id"]
        return RequestSpec("GET", template, params)

    return build


def _policy_post(path: str, *, project_in_body: bool) -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        body: dict[str, object] = {"document": POLICY_DOC}
        if project_in_body:
            body["project_id"] = overrides.get("project_id", side.project_id)
        elif "project_id" in overrides:
            body["project_id"] = overrides["project_id"]
        return RequestSpec("POST", path, content=json.dumps(body).encode(), headers=JSON)

    return build


def matrix_intent(side: Side, arguments: dict[str, object] | None = None) -> dict[str, object]:
    """An action intent in the tenant's seeded run (the gate rule is in the world's policy)."""
    return {
        "run_id": side.run_id, "agent_id": side.agent_slug, "capability": "matrix.gate",
        "resource": "r", "risk_class": "R2", "arguments": arguments or {"k": "v"},
    }  # fmt: skip


def _decide(side: Side, overrides: dict[str, str]) -> RequestSpec:
    """Ask for an allowed action in a fresh run: valid for every key of any project."""
    body = {
        "run_id": new_id(IdKind.RUN), "agent_id": side.agent_slug, "capability": "matrix.ok",
        "resource": "r", "risk_class": "R0", "arguments": {},
    }  # fmt: skip
    return RequestSpec("POST", "/v1/policy/decide", content=json.dumps(body).encode(), headers=JSON)


def _approval(method: str, suffix: str = "", *, fresh: str, body: str | None = None) -> Builder:
    """A call on an approval. With an id (the cross-workspace probes) it targets that id, else the
    world makes a fresh approval (`fresh`: "approval" waiting, "approved" approved and unspent)
    and puts its id where the path says FRESH. `body`: "decision" or "consume"."""

    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        ident = overrides.get("approval_id")
        content: bytes | None = None
        if body == "decision":
            content = json.dumps({"decision": "approve"}).encode()
        elif body == "consume":
            # FRESH_INTENT is replaced by the intent the world made the fresh approval for.
            intent = json.dumps(matrix_intent(side)).encode() if ident else b'"FRESH_INTENT"'
            content = b'{"intent": ' + intent + b"}"
        return RequestSpec(
            method, f"/v1/approvals/{ident or 'FRESH'}{suffix}",
            params={"timeout": "0"} if suffix == "/wait" else {},
            content=content, headers=JSON if content else {},
            fresh=None if ident else fresh,
        )  # fmt: skip

    return build


def _target(
    method: str, prefix: str, param: str, kind: str, body: dict[str, str] | None
) -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        ident = overrides.get(param)
        return RequestSpec(
            method, f"{prefix}/{ident or 'FRESH'}",
            content=json.dumps(body).encode() if body is not None else None,
            headers=JSON if body is not None else {},
            fresh=None if ident else kind,
        )  # fmt: skip

    return build


def _post_invitation(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = {"email": f"invitee-{uuid.uuid4().hex[:10]}@acme.test", "role": "VIEWER"}
    return RequestSpec("POST", "/v1/invitations", content=json.dumps(body).encode(), headers=JSON)


ADMINS = ("owner", "dual")  # people who can reach the route and are members of both workspaces
# People who read alert rules (alert.read) and are members of both workspaces.
ALERT_READERS = ("owner", "dual", "developer")


def _post_key(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = {"name": f"probe-{uuid.uuid4().hex[:8]}", "scopes": ["runs:read"]}
    return RequestSpec("POST", "/v1/api-keys", content=json.dumps(body).encode(), headers=JSON)


def _post_override(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = {"model_pattern": f"probe-{uuid.uuid4().hex[:8]}*", "input_per_million": "1.5",
            "output_per_million": 3}  # fmt: skip
    return RequestSpec(
        "POST", "/v1/pricing/overrides", content=json.dumps(body).encode(), headers=JSON
    )


def _post_rebuild(side: Side, overrides: dict[str, str]) -> RequestSpec:
    return RequestSpec("POST", "/v1/cost/rebuild", content=b"{}", headers=JSON)


def _post_evaluator(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = {"name": f"probe-{uuid.uuid4().hex[:10]}", "kind": "deterministic",
            "implementation": "run_succeeded"}  # fmt: skip
    return RequestSpec("POST", "/v1/evaluators", content=json.dumps(body).encode(), headers=JSON)


def _post_evaluation(side: Side, overrides: dict[str, str]) -> RequestSpec:
    ids = _ids(side, overrides)
    body = {"evaluator_id": ids["evaluator_id"]}
    return RequestSpec(
        "POST", f"/v1/runs/{ids['run_id']}/evaluations", content=json.dumps(body).encode(),
        headers=JSON,
    )  # fmt: skip


def _post_verdict(side: Side, overrides: dict[str, str]) -> RequestSpec:
    ids = _ids(side, overrides)
    body = {"evaluator_id": ids["human_evaluator_id"], "label": "pass", "explanation": "ok"}
    return RequestSpec(
        "POST", f"/v1/runs/{ids['run_id']}/verdicts", content=json.dumps(body).encode(),
        headers=JSON,
    )  # fmt: skip


def _compare(side: Side, overrides: dict[str, str]) -> RequestSpec:
    ids = _ids(side, overrides)
    return RequestSpec(
        "GET", f"/v1/runs/{ids['run_id']}/compare", params={"with_run_id": ids["with_run_id"]}
    )


def _post_experiment(side: Side, overrides: dict[str, str]) -> RequestSpec:
    body = {"name": f"probe-{uuid.uuid4().hex[:8]}",
            "project_id": overrides.get("project_id", side.project_id)}  # fmt: skip
    return RequestSpec("POST", "/v1/experiments", content=json.dumps(body).encode(), headers=JSON)


def _on_experiment(
    method: str, suffix: str = "", body: Callable[[dict[str, str]], dict[str, object]] | None = None
) -> Builder:
    """A call on the seeded experiment; `suffix` may name Side fields, `body` builds the JSON."""

    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        ids = _ids(side, overrides)
        content = json.dumps(body(ids)).encode() if body else None
        return RequestSpec(
            method, f"/v1/experiments/{ids['experiment_id']}{suffix.format(**ids)}",
            content=content, headers=JSON if content else {},
        )  # fmt: skip

    return build


def _variant_body(ids: dict[str, str]) -> dict[str, object]:
    return {"name": f"c-{uuid.uuid4().hex[:8]}", "role": "candidate", "config": {"model": "x"}}


def _attach_body(ids: dict[str, str]) -> dict[str, object]:
    return {"run_id": ids["with_run_id"], "variant_id": ids["variant_id"]}


def _alert_destination(method: str, *, fresh: str | None = None) -> Builder:
    """POST creates a destination; the others act on the seeded one (DELETE: a fresh one)."""

    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        ident = overrides.get("destination_id")
        if method == "POST":
            hook = f"https://hooks.slack.com/services/T0/B0/{uuid.uuid4().hex}"
            body = {
                "name": f"probe-{uuid.uuid4().hex[:8]}",
                "type": "slack",
                "target": {"url": hook},
            }
            return RequestSpec(
                "POST", "/v1/alerts/destinations", content=json.dumps(body).encode(), headers=JSON
            )
        if method == "LIST":
            return RequestSpec("GET", "/v1/alerts/destinations")
        path = f"/v1/alerts/destinations/{ident or side.destination_id}"
        if method == "TEST":
            return RequestSpec("POST", path + "/test")
        if method == "DELETE" and ident is None:
            path = "/v1/alerts/destinations/FRESH"
        content = (
            json.dumps({"name": f"renamed-{uuid.uuid4().hex[:8]}"}).encode()
            if method == "PATCH"
            else None
        )
        return RequestSpec(
            "GET" if method == "LIST" else method, path, content=content,
            headers=JSON if content else {},
            fresh=fresh if (method == "DELETE" and ident is None) else None,
        )  # fmt: skip

    return build


def _alert_rule(method: str, *, fresh: str | None = None) -> Builder:
    def build(side: Side, overrides: dict[str, str]) -> RequestSpec:
        ident = overrides.get("rule_id")
        if method == "POST":
            target = overrides.get("destination_id", side.destination_id)
            body = {"name": f"probe-{uuid.uuid4().hex[:8]}", "trigger": "run_failed",
                    "destination_ids": [target]}  # fmt: skip
            return RequestSpec(
                "POST", "/v1/alerts/rules", content=json.dumps(body).encode(), headers=JSON
            )
        if method == "LIST":
            return RequestSpec("GET", "/v1/alerts/rules")
        path = f"/v1/alerts/rules/{ident or side.rule_id}"
        if method == "DELETE" and ident is None:
            path = "/v1/alerts/rules/FRESH"
        if method == "PREVIEW":
            path += "/preview"
        content = json.dumps({"cooldown_seconds": 60}).encode() if method == "PATCH" else None
        return RequestSpec(
            "GET" if method in ("LIST", "PREVIEW") else method, path, content=content,
            headers=JSON if content else {},
            fresh=fresh if (method == "DELETE" and ident is None) else None,
        )  # fmt: skip

    return build


_ANALYTICS = ("summary", "cost", "reliability", "performance", "waste")

CASES: dict[tuple[str, str], RouteCase] = {
    c.key: c
    for c in [
        RouteCase("POST", "/v1/events", actions.EVENT_WRITE, _post_event, 202),
        RouteCase("POST", "/v1/events/batch", actions.EVENT_WRITE, _post_batch, 202),
        RouteCase("POST", "/v1/runs", actions.RUN_WRITE, _post_run, 201),
        RouteCase("GET", "/v1/runs", actions.RUN_READ, _get("/v1/runs"), 200, ("project_id",)),
        RouteCase(
            "GET", "/v1/runs/{run_id}", actions.RUN_READ, _get("/v1/runs/{run_id}"), 200,
            ("run_id",),
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/events", actions.RUN_READ,
            _get("/v1/runs/{run_id}/events"), 200, ("run_id",),
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/events/{event_id}", actions.RUN_READ,
            _get("/v1/runs/{run_id}/events/{event_id}"), 200, ("run_id", "event_id"),
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/spans", actions.RUN_READ,
            _get("/v1/runs/{run_id}/spans"), 200, ("run_id",),
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/findings", actions.RUN_READ,
            _get("/v1/runs/{run_id}/findings"), 200, ("run_id",),
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/stream", actions.RUN_READ,
            _get("/v1/runs/{run_id}/stream"), 200, ("run_id",), transport="socket",
        ),
        RouteCase(
            "GET", "/v1/security/findings", actions.RUN_READ, _get("/v1/security/findings"), 200,
            ("project_id",),
        ),
        RouteCase(
            "GET", "/v1/security/detectors", actions.RUN_READ, _get("/v1/security/detectors"), 200,
            ("project_id",),
        ),
        RouteCase(
            "GET", "/v1/projects/{project_id}/security-settings", actions.PROJECT_WRITE,
            _security_settings("GET"), 200, ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "PUT", "/v1/projects/{project_id}/security-settings", actions.PROJECT_WRITE,
            _security_settings("PUT"), 200, ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/projects/{project_id}/quotas", actions.PROJECT_READ,
            _quotas("GET"), 200, ("project_id",),
        ),
        RouteCase(
            "PUT", "/v1/projects/{project_id}/quotas", actions.PROJECT_WRITE,
            _quotas("PUT"), 200, ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/workspaces/{workspace_id}/retention", actions.RETENTION_READ,
            _retention("GET"), 200, ("workspace_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "PUT", "/v1/workspaces/{workspace_id}/retention", actions.RETENTION_WRITE,
            _retention("PUT"), 200, ("workspace_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/workspaces/{workspace_id}/retention/preview", actions.RETENTION_READ,
            _retention("GET", "/preview"), 200, ("workspace_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/workspaces/{workspace_id}/retention/reports", actions.RETENTION_READ,
            _retention("GET", "/reports"), 200, ("workspace_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/policies/current", actions.POLICY_READ, _policy_get("/v1/policies/current"),
            200, ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/policies/versions", actions.POLICY_READ,
            _policy_get("/v1/policies/versions"), 200, ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/policies/versions/{version}", actions.POLICY_READ,
            _policy_get("/v1/policies/versions/1"), 200, ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/policies/diff", actions.POLICY_READ,
            _policy_get("/v1/policies/diff", {"from_version": "1", "to_version": "2"}), 200,
            ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/policies/validate", actions.POLICY_WRITE,
            _policy_post("/v1/policies/validate", project_in_body=False), 200,
        ),
        RouteCase(
            "POST", "/v1/policies/publish", actions.POLICY_WRITE,
            _policy_post("/v1/policies/publish", project_in_body=False), 201,
            ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/policies/dry-run", actions.POLICY_WRITE,
            _policy_post("/v1/policies/dry-run", project_in_body=True), 200,
            ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase("POST", "/v1/policy/decide", actions.POLICY_DECIDE, _decide, 200),
        RouteCase(
            "GET", "/v1/policies/fail-modes", actions.POLICY_FAIL_MODES,
            _policy_get("/v1/policies/fail-modes"), 200, ("project_id",),
            probe_actors=("writer", "wide", "owner", "dual", "other"),
        ),
        RouteCase(
            "GET", "/v1/approvals/{approval_id}/wait", actions.APPROVAL_USE,
            _approval("GET", "/wait", fresh="approval"), 200, ("approval_id",),
            probe_actors=("writer", "other"),
        ),
        RouteCase(
            "POST", "/v1/approvals/{approval_id}/consume", actions.APPROVAL_USE,
            _approval("POST", "/consume", fresh="approved", body="consume"), 200,
            ("approval_id",), probe_actors=("writer", "other"),
        ),
        RouteCase(
            "DELETE", "/v1/approvals/{approval_id}", actions.APPROVAL_USE,
            _approval("DELETE", fresh="approval"), 204, ("approval_id",),
            probe_actors=("writer", "other"),
        ),
        RouteCase(
            "GET", "/v1/approvals", actions.APPROVAL_READ, _get("/v1/approvals"), 200,
            ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/approvals/pending-count", actions.APPROVAL_READ,
            _get("/v1/approvals/pending-count"), 200,
        ),
        RouteCase(
            "GET", "/v1/approvals/{approval_id}", actions.APPROVAL_READ,
            _get("/v1/approvals/{approval_id}"), 200, ("approval_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/approvals/{approval_id}/decision", actions.APPROVAL_DECIDE,
            _approval("POST", "/decision", fresh="approval", body="decision"), 200,
            ("approval_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/alerts/destinations", actions.ALERT_WRITE, _alert_destination("POST"),
            201,
        ),
        RouteCase(
            "GET", "/v1/alerts/destinations", actions.ALERT_DESTINATION_READ,
            _alert_destination("LIST"), 200,
        ),
        RouteCase(
            "GET", "/v1/alerts/destinations/{destination_id}", actions.ALERT_DESTINATION_READ,
            _alert_destination("GET"), 200, ("destination_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "PATCH", "/v1/alerts/destinations/{destination_id}", actions.ALERT_WRITE,
            _alert_destination("PATCH"), 200, ("destination_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/alerts/destinations/{destination_id}/test", actions.ALERT_WRITE,
            _alert_destination("TEST"), 200, ("destination_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "DELETE", "/v1/alerts/destinations/{destination_id}", actions.ALERT_WRITE,
            _alert_destination("DELETE", fresh="alert_destination"), 204, ("destination_id",),
            probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/alerts/rules", actions.ALERT_WRITE, _alert_rule("POST"), 201,
            ("destination_id",), probe_actors=ADMINS,
        ),
        RouteCase("GET", "/v1/alerts/rules", actions.ALERT_READ, _alert_rule("LIST"), 200),
        RouteCase(
            "GET", "/v1/alerts/rules/{rule_id}", actions.ALERT_READ, _alert_rule("GET"),
            200, ("rule_id",), probe_actors=ALERT_READERS,
        ),
        RouteCase(
            "PATCH", "/v1/alerts/rules/{rule_id}", actions.ALERT_WRITE,
            _alert_rule("PATCH"), 200, ("rule_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "DELETE", "/v1/alerts/rules/{rule_id}", actions.ALERT_WRITE,
            _alert_rule("DELETE", fresh="alert_rule"), 204, ("rule_id",),
            probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/alerts/rules/{rule_id}/preview", actions.ALERT_READ,
            _alert_rule("PREVIEW"), 200, ("rule_id",), probe_actors=ALERT_READERS,
        ),
        RouteCase(
            "GET", "/v1/alerts/notifications", actions.ALERT_READ,
            _get("/v1/alerts/notifications"), 200,
        ),
        RouteCase("GET", "/v1/projects", actions.PROJECT_READ, _list_projects, 200),
        RouteCase("POST", "/v1/projects", actions.PROJECT_WRITE, _create_project, 201),
        RouteCase(
            "GET", "/v1/pricing", actions.PRICING_READ, _get("/v1/pricing"), 200, ("project_id",)
        ),
        *[
            RouteCase(
                "GET", f"/v1/analytics/{name}", actions.ANALYTICS_READ,
                _get(f"/v1/analytics/{name}"), 200, ("project_id",),
            )
            for name in _ANALYTICS
        ],
        RouteCase("GET", "/v1/members", actions.MEMBER_READ, _get("/v1/members"), 200),
        RouteCase(
            "PATCH", "/v1/members/{user_id}", actions.MEMBER_WRITE,
            _target("PATCH", "/v1/members", "user_id", "member", {"role": "VIEWER"}), 200,
            ("user_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "DELETE", "/v1/members/{user_id}", actions.MEMBER_WRITE,
            _target("DELETE", "/v1/members", "user_id", "member", None), 204,
            ("user_id",), probe_actors=ADMINS,
        ),
        RouteCase("GET", "/v1/invitations", actions.INVITE_READ, _get("/v1/invitations"), 200),
        RouteCase("POST", "/v1/invitations", actions.INVITE_WRITE, _post_invitation, 201),
        RouteCase(
            "DELETE", "/v1/invitations/{invitation_id}", actions.INVITE_WRITE,
            _target("DELETE", "/v1/invitations", "invitation_id", "invitation", None), 204,
            ("invitation_id",), probe_actors=ADMINS,
        ),
        RouteCase("GET", "/v1/api-keys", actions.API_KEY_READ, _get("/v1/api-keys"), 200),
        RouteCase("POST", "/v1/api-keys", actions.API_KEY_CREATE, _post_key, 201),
        RouteCase(
            "DELETE", "/v1/api-keys/{key_id}", actions.API_KEY_REVOKE,
            _target("DELETE", "/v1/api-keys", "key_id", "api_key", None), 204,
            ("key_id",), probe_actors=ADMINS,
        ),
        RouteCase("GET", "/v1/audit", actions.AUDIT_READ, _get("/v1/audit"), 200),
        RouteCase(
            "GET", "/v1/evaluators", actions.EVALUATION_READ, _get("/v1/evaluators"), 200
        ),
        RouteCase("POST", "/v1/evaluators", actions.EVALUATOR_WRITE, _post_evaluator, 201),
        RouteCase(
            "DELETE", "/v1/evaluators/{evaluator_id}", actions.EVALUATOR_WRITE,
            _target("DELETE", "/v1/evaluators", "evaluator_id", "evaluator", None), 204,
            ("evaluator_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/compare", actions.RUN_READ, _compare, 200,
            ("run_id", "with_run_id"),
        ),
        RouteCase(
            "GET", "/v1/runs/{run_id}/evaluations", actions.EVALUATION_READ,
            _get("/v1/runs/{run_id}/evaluations"), 200, ("run_id",),
        ),
        RouteCase(
            "POST", "/v1/runs/{run_id}/evaluations", actions.EVALUATION_RUN, _post_evaluation, 202,
            ("run_id", "evaluator_id"), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/runs/{run_id}/verdicts", actions.EVALUATION_LABEL, _post_verdict, 201,
            ("run_id", "human_evaluator_id"), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/experiments", actions.EXPERIMENT_READ, _get("/v1/experiments"), 200,
            ("project_id",),
        ),
        RouteCase(
            "POST", "/v1/experiments", actions.EXPERIMENT_WRITE, _post_experiment, 201,
            ("project_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "GET", "/v1/experiments/{experiment_id}", actions.EXPERIMENT_READ,
            _get("/v1/experiments/{experiment_id}"), 200, ("experiment_id",),
        ),
        RouteCase(
            "POST", "/v1/experiments/{experiment_id}/variants", actions.EXPERIMENT_WRITE,
            _on_experiment("POST", "/variants", _variant_body), 201,
            ("experiment_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/experiments/{experiment_id}/runs", actions.EXPERIMENT_WRITE,
            _on_experiment("POST", "/runs", _attach_body),
            201, ("experiment_id", "with_run_id", "variant_id"), probe_actors=ADMINS,
        ),
        RouteCase(
            "POST", "/v1/experiments/{experiment_id}/evaluate", actions.EVALUATION_RUN,
            _on_experiment("POST", "/evaluate"), 202, ("experiment_id",), probe_actors=ADMINS,
        ),
        RouteCase(
            "DELETE", "/v1/experiments/{experiment_id}/runs/{run_id}", actions.EXPERIMENT_WRITE,
            _on_experiment("DELETE", "/runs/{run_id}"), 204, ("experiment_id", "run_id"),
            probe_actors=ADMINS,
        ),
        RouteCase(
            "DELETE", "/v1/experiments/{experiment_id}", actions.EXPERIMENT_WRITE,
            _on_experiment("DELETE"), 204, ("experiment_id",), probe_actors=ADMINS,
        ),
        RouteCase("POST", "/v1/pricing/overrides", actions.PRICING_WRITE, _post_override, 201),
        RouteCase("POST", "/v1/cost/rebuild", actions.PRICING_WRITE, _post_rebuild, 202),
        RouteCase("PUT", "/v1/artifacts/{artifact_id}", actions.ARTIFACT_WRITE, _put_artifact, 201),
        RouteCase(
            "GET", "/v1/artifacts/{artifact_id}", actions.RUN_READ,
            _get("/v1/artifacts/{artifact_id}"), 200, ("artifact_id",),
        ),
        RouteCase(
            "GET", "/v1/artifacts/{artifact_id}/content", actions.PAYLOAD_READ,
            _get("/v1/artifacts/{artifact_id}/content"), 200, ("artifact_id",),
        ),
    ]
}  # fmt: skip
