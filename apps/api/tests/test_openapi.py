"""The committed OpenAPI file is the reviewable API contract; it must match the code."""

import json
from typing import Any

from abb_api import openapi


def operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, op) for path, ops in document["paths"].items() for method, op in ops.items()
    ]


def test_the_committed_file_is_current() -> None:
    assert openapi.main(["--check"]) == 0


def test_the_document_is_deterministic() -> None:
    assert openapi.render(openapi.build_document()) == openapi.render(openapi.build_document())


BEARER: list[dict[str, list[str]]] = [{"bearerAuth": []}]
EITHER: list[dict[str, list[str]]] = [{"bearerAuth": []}, {"sessionCookie": []}]
# Which credentials may call what (the single place a reviewer reads it). Everything not listed
# here is a read that a key or a signed-in person may call.
EXPECTED_SECURITY: dict[tuple[str, str], list[dict[str, list[str]]]] = {
    ("post", "/v1/events"): BEARER,
    ("post", "/v1/events/batch"): BEARER,
    ("post", "/v1/runs"): BEARER,
    ("put", "/v1/artifacts/{artifact_id}"): BEARER,
    ("get", "/v1/me"): [{"sessionCookie": []}],
    ("get", "/v1/auth/login"): [],
    ("get", "/v1/auth/callback"): [],
    ("post", "/v1/auth/logout"): [{}, {"sessionCookie": []}],
}


def test_every_v1_operation_is_authenticated_and_described() -> None:
    document = openapi.build_document()
    schemes = document["components"]["securitySchemes"]
    assert schemes["bearerAuth"]["scheme"] == "bearer"
    assert schemes["sessionCookie"]["in"] == "cookie"
    seen = 0
    for path, method, op in operations(document):
        if path.startswith("/v1/"):
            seen += 1
            assert op["security"] == EXPECTED_SECURITY.get((method, path), EITHER), (method, path)
            assert op["summary"] and 401 in map(int, op["responses"]), (method, path)
            has_header = any(p["name"] == "X-ABB-Workspace" for p in op.get("parameters", []))
            assert has_header == (op["security"] == EITHER), (method, path)
        else:
            assert "security" not in op, (method, path)  # health endpoints are public
    assert seen == 23


def test_error_responses_use_the_real_envelope_not_fastapis_default() -> None:
    document = openapi.build_document()
    text = json.dumps(document)
    assert "HTTPValidationError" not in text and "ValidationError" not in text
    for path, method, op in operations(document):
        for status, response in op["responses"].items():
            if int(status) >= 400:
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}, (
                    method,
                    path,
                    status,
                )


def test_the_public_surface_is_exactly_what_the_plan_promises() -> None:
    document = openapi.build_document()
    surface = {(m.upper(), p) for p, m, _ in operations(document)}
    assert surface == {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/v1/auth/login"),
        ("GET", "/v1/auth/callback"),
        ("POST", "/v1/auth/logout"),
        ("GET", "/v1/me"),
        ("GET", "/v1/projects"),
        ("POST", "/v1/projects"),
        ("POST", "/v1/events"),
        ("POST", "/v1/events/batch"),
        ("POST", "/v1/runs"),
        ("GET", "/v1/runs"),
        ("GET", "/v1/runs/{run_id}"),
        ("GET", "/v1/runs/{run_id}/events"),
        ("GET", "/v1/runs/{run_id}/events/{event_id}"),
        ("GET", "/v1/runs/{run_id}/spans"),
        ("GET", "/v1/runs/{run_id}/stream"),
        ("GET", "/v1/pricing"),
        ("GET", "/v1/analytics/summary"),
        ("GET", "/v1/analytics/cost"),
        ("GET", "/v1/analytics/reliability"),
        ("GET", "/v1/analytics/performance"),
        ("PUT", "/v1/artifacts/{artifact_id}"),
        ("GET", "/v1/artifacts/{artifact_id}"),
        ("GET", "/v1/artifacts/{artifact_id}/content"),
    }  # a new route must be added here deliberately, with its auth and error docs reviewed
