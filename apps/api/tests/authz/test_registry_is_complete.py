"""Fail closed: a route without an authorization case, or without `require(action)`, fails CI."""

from fastapi import FastAPI

from abb_api.core.config import Settings
from abb_api.main import create_app
from abb_api.openapi import build_document
from tests.authz.registry import CASES, PUBLIC
from tests.authz.routes import enumerate_routes

# Counted by hand when the registry was written; a mismatch means the walker or the registry moved.
EXPECTED_V1_OPERATIONS = 17


def _app() -> FastAPI:
    return create_app(
        Settings(database_url="postgresql+asyncpg://x:x@127.0.0.1:1/x", environment="test")
    )


def problems(app: FastAPI) -> list[str]:
    """Everything wrong with the app's routes, as readable lines (empty when complete)."""
    found: list[str] = []
    seen: set[tuple[str, str]] = set()
    for route in enumerate_routes(app):
        if route.kind != "http":
            found.append(f"{route.describe()}: mounts, websockets and unknown routes need a review")
            continue
        seen.add(route.key)
        if route.key in PUBLIC:
            if route.required_actions:
                found.append(f"{route.describe()}: listed PUBLIC but demands an action")
            continue
        case = CASES.get(route.key)
        if case is None:
            found.append(f"{route.describe()}: no RouteCase in tests/authz/registry.py")
        elif route.required_actions != {case.action}:
            found.append(
                f"{route.describe()}: route requires {sorted(route.required_actions)}, "
                f"case says {case.action!r}"
            )
    found.extend(
        f"{m} {p}: RouteCase for a route that does not exist" for m, p in CASES.keys() - seen
    )
    found.extend(f"{m} {p}: PUBLIC entry for a route that does not exist" for m, p in PUBLIC - seen)
    return found


def test_every_route_has_an_authorization_case() -> None:
    assert problems(_app()) == []


def test_an_unregistered_route_is_reported_even_when_hidden_from_openapi() -> None:
    app = _app()

    async def probe() -> dict[str, str]:  # pragma: no cover - never called
        return {}

    app.add_api_route("/v1/probe", probe, methods=["GET"], include_in_schema=False)
    assert any(line.startswith("GET /v1/probe") for line in problems(app))


def test_a_route_that_forgets_require_is_reported() -> None:
    app = _app()

    async def leak() -> dict[str, str]:  # pragma: no cover - never called
        return {}

    app.add_api_route("/v1/runs/{run_id}/leak", leak, methods=["GET"])
    assert any("/v1/runs/{run_id}/leak" in line for line in problems(app))


def test_mounts_and_websockets_are_reported() -> None:
    from starlette.applications import Starlette

    app = _app()
    app.mount("/sidecar", Starlette())

    async def socket(websocket: object) -> None:  # pragma: no cover - never called
        return None

    app.add_api_websocket_route("/v1/live", socket)
    lines = problems(app)
    assert any(line.startswith("MOUNT /sidecar") for line in lines)
    assert any(line.startswith("WEBSOCKET /v1/live") for line in lines)


def test_the_openapi_document_and_the_registry_agree_both_ways() -> None:
    document = build_document()
    documented = {(m.upper(), p) for p, ops in document["paths"].items() for m in ops}
    assert documented - PUBLIC == set(CASES), "OpenAPI and tests/authz/registry.py disagree"
    assert len([k for k in CASES if k[1].startswith("/v1/")]) == EXPECTED_V1_OPERATIONS


def test_http_head_is_not_a_second_door() -> None:
    """Starlette adds HEAD to plain routes (docs) but not to API routes: no unauthenticated HEAD."""
    heads = {r.path for r in enumerate_routes(_app()) if r.method == "HEAD"}
    assert not any(p.startswith("/v1/") for p in heads)
