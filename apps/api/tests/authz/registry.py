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

# Routes that need a signed-in person but no particular workspace and no action.
SESSION_ONLY: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/v1/me"), ("POST", "/v1/invitations/accept")}
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
    every_id: frozenset[str]  # every id the tenant owns (for leak scans)


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # "member" | "invitation": the world creates a fresh target in acme and puts its id where the
    # path says FRESH, so a mutating request is valid for every actor that may send it.
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
_ANALYTICS = ("summary", "cost", "reliability", "performance")

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
            "GET", "/v1/runs/{run_id}/stream", actions.RUN_READ,
            _get("/v1/runs/{run_id}/stream"), 200, ("run_id",), transport="socket",
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
