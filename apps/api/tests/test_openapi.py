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
    # Phase 14 step 2: the agent side of decisions and approvals is for keys only.
    ("post", "/v1/policy/decide"): BEARER,
    ("get", "/v1/approvals/{approval_id}/wait"): BEARER,
    ("post", "/v1/approvals/{approval_id}/consume"): BEARER,
    ("delete", "/v1/approvals/{approval_id}"): BEARER,
    ("get", "/v1/me"): [{"sessionCookie": []}],
    ("post", "/v1/invitations/accept"): [{"sessionCookie": []}],
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
    # main's 37, Phase 11's 15, Phase 22's /v1/analytics/waste, Phase 13's four security routes and
    # Phase 14's seven policy routes, and step 2's nine (decide, fail-modes, wait, consume, cancel,
    # list, pending count, detail, decision), and Phase 17's thirteen alerting routes (step 1's
    # twelve, step 2's send-test)
    assert seen == 37 + 15 + 1 + 4 + 7 + 9 + 13


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
        ("POST", "/v1/pricing/overrides"),
        ("POST", "/v1/cost/rebuild"),
        ("GET", "/v1/audit"),
        ("GET", "/v1/api-keys"),
        ("POST", "/v1/api-keys"),
        ("DELETE", "/v1/api-keys/{key_id}"),
        ("GET", "/v1/members"),
        ("PATCH", "/v1/members/{user_id}"),
        ("DELETE", "/v1/members/{user_id}"),
        ("GET", "/v1/invitations"),
        ("POST", "/v1/invitations"),
        ("DELETE", "/v1/invitations/{invitation_id}"),
        ("POST", "/v1/invitations/accept"),
        ("POST", "/v1/events"),
        ("POST", "/v1/events/batch"),
        ("POST", "/v1/runs"),
        ("GET", "/v1/runs"),
        ("GET", "/v1/runs/{run_id}"),
        ("GET", "/v1/runs/{run_id}/events"),
        ("GET", "/v1/runs/{run_id}/events/{event_id}"),
        ("GET", "/v1/runs/{run_id}/spans"),
        ("GET", "/v1/runs/{run_id}/findings"),
        ("GET", "/v1/security/findings"),
        ("GET", "/v1/security/detectors"),
        ("GET", "/v1/projects/{project_id}/security-settings"),
        ("PUT", "/v1/projects/{project_id}/security-settings"),
        ("GET", "/v1/policies/current"),
        ("GET", "/v1/policies/versions"),
        ("GET", "/v1/policies/versions/{version}"),
        ("GET", "/v1/policies/diff"),
        ("POST", "/v1/policies/validate"),
        ("POST", "/v1/policies/publish"),
        ("POST", "/v1/policies/dry-run"),
        ("GET", "/v1/policies/fail-modes"),
        ("POST", "/v1/policy/decide"),
        ("POST", "/v1/alerts/destinations"),
        ("GET", "/v1/alerts/destinations"),
        ("GET", "/v1/alerts/destinations/{destination_id}"),
        ("PATCH", "/v1/alerts/destinations/{destination_id}"),
        ("POST", "/v1/alerts/destinations/{destination_id}/test"),
        ("DELETE", "/v1/alerts/destinations/{destination_id}"),
        ("POST", "/v1/alerts/rules"),
        ("GET", "/v1/alerts/rules"),
        ("GET", "/v1/alerts/rules/{rule_id}"),
        ("PATCH", "/v1/alerts/rules/{rule_id}"),
        ("DELETE", "/v1/alerts/rules/{rule_id}"),
        ("GET", "/v1/alerts/rules/{rule_id}/preview"),
        ("GET", "/v1/alerts/notifications"),
        ("GET", "/v1/approvals"),
        ("GET", "/v1/approvals/pending-count"),
        ("GET", "/v1/approvals/{approval_id}"),
        ("GET", "/v1/approvals/{approval_id}/wait"),
        ("POST", "/v1/approvals/{approval_id}/consume"),
        ("POST", "/v1/approvals/{approval_id}/decision"),
        ("DELETE", "/v1/approvals/{approval_id}"),
        ("GET", "/v1/runs/{run_id}/stream"),
        ("GET", "/v1/pricing"),
        ("GET", "/v1/analytics/summary"),
        ("GET", "/v1/analytics/cost"),
        ("GET", "/v1/analytics/reliability"),
        ("GET", "/v1/analytics/performance"),
        ("GET", "/v1/analytics/waste"),
        ("PUT", "/v1/artifacts/{artifact_id}"),
        ("GET", "/v1/artifacts/{artifact_id}"),
        ("GET", "/v1/artifacts/{artifact_id}/content"),
        # Phase 11 (ADR-090): evaluator definitions.
        ("GET", "/v1/evaluators"),
        ("POST", "/v1/evaluators"),
        ("DELETE", "/v1/evaluators/{evaluator_id}"),
        ("GET", "/v1/experiments"),
        ("POST", "/v1/experiments"),
        ("GET", "/v1/experiments/{experiment_id}"),
        ("DELETE", "/v1/experiments/{experiment_id}"),
        ("POST", "/v1/experiments/{experiment_id}/variants"),
        ("POST", "/v1/experiments/{experiment_id}/runs"),
        ("DELETE", "/v1/experiments/{experiment_id}/runs/{run_id}"),
        ("POST", "/v1/experiments/{experiment_id}/evaluate"),
        ("GET", "/v1/runs/{run_id}/compare"),
        ("GET", "/v1/runs/{run_id}/evaluations"),
        ("POST", "/v1/runs/{run_id}/evaluations"),
        ("POST", "/v1/runs/{run_id}/verdicts"),
    }  # a new route must be added here deliberately, with its auth and error docs reviewed
