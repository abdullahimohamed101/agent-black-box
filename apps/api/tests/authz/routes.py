"""Enumerate every route the application serves, not only those the OpenAPI document shows.

The OpenAPI document hides routes registered with `include_in_schema=False`, mounted sub-apps and
websockets, which is exactly where an unprotected endpoint would hide. This walker reads the
router tree itself and treats anything it does not recognise as a finding (fail closed).
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route, WebSocketRoute


@dataclass(frozen=True)
class RouteInfo:
    kind: str  # "http" | "mount" | "websocket" | "unknown"
    method: str  # "" for non-http kinds
    path: str
    required_actions: frozenset[str] = field(default_factory=frozenset)
    requires_session: bool = False
    in_schema: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def describe(self) -> str:
        return f"{self.method or self.kind.upper()} {self.path}"


def _requires_session(dependant: Dependant | None) -> bool:
    stack = [dependant] if dependant is not None else []
    while stack:
        node = stack.pop()
        if getattr(node.call, "requires_session", False):
            return True
        stack.extend(node.dependencies)
    return False


def _actions_required(dependant: Dependant | None) -> frozenset[str]:
    """The actions demanded by `require(action)` dependencies anywhere in the dependant tree."""
    found: set[str] = set()
    stack = [dependant] if dependant is not None else []
    while stack:
        node = stack.pop()
        action = getattr(node.call, "required_action", None)
        if isinstance(action, str):
            found.add(action)
        stack.extend(node.dependencies)
    return frozenset(found)


def _http(
    route: Any, path: str, methods: set[str], dependant: Any, in_schema: bool
) -> Iterator[RouteInfo]:
    required = _actions_required(dependant)
    for method in sorted(methods):
        yield RouteInfo("http", method, path, required, _requires_session(dependant), in_schema)


def _walk(routes: list[Any], prefix: str = "") -> Iterator[RouteInfo]:
    for route in routes:
        name = type(route).__name__
        if name == "_IncludedRouter":
            # FastAPI wraps included routers; their effective routes carry the final path.
            yield from _walk(
                [*route.effective_candidates(), *route.effective_low_priority_routes()], prefix
            )
        elif name == "_EffectiveRouteContext":
            original = route.original_route
            if isinstance(original, (APIWebSocketRoute, WebSocketRoute)):
                yield RouteInfo("websocket", "", prefix + route.path)
            elif isinstance(original, APIRoute):
                yield from _http(
                    route, prefix + route.path, set(route.methods), original.dependant,
                    bool(route.include_in_schema),
                )  # fmt: skip
            else:
                yield RouteInfo("unknown", "", prefix + route.path)
        elif isinstance(route, APIRoute):
            yield from _http(route, prefix + route.path, set(route.methods or ()), route.dependant,
                             bool(route.include_in_schema))  # fmt: skip
        elif isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
            yield RouteInfo("websocket", "", prefix + route.path)
        elif isinstance(route, Route):  # docs, redoc, openapi.json
            yield from _http(route, prefix + route.path, set(route.methods or ()), None,
                             bool(route.include_in_schema))  # fmt: skip
        elif isinstance(route, Mount):
            yield RouteInfo("mount", "", prefix + route.path)
        else:
            yield RouteInfo("unknown", "", prefix + getattr(route, "path", name))


def enumerate_routes(app: FastAPI) -> list[RouteInfo]:
    return list(_walk(list(app.routes)))
