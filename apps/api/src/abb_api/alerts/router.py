"""Alerting routes (Phase 17, ADR-150): destinations, rules, the rule preview and the delivery
history. For signed-in people only: no API key holds an alert action.

* Destinations and every change: owners and admins (`alert.write`, `alert.destination.read`).
* Rules, preview and history: every role that reads runs (`alert.read`); the destination part of a
  rule and of a history row is shown only to those who may read destinations.

A target URL or a recipient list is stored encrypted and never returned; `masked_target` is the
only display form. A webhook's signing secret is returned once, when it is created or rotated.
"""

from datetime import datetime
from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request, Response

from abb_api.alerts import schemas as s
from abb_api.alerts.service import AlertsService
from abb_api.authz import actions
from abb_api.authz.audit import authorize_audited
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope

router = APIRouter(tags=["alerts"])

Reader = Annotated[Principal, Depends(require(actions.ALERT_READ))]
Writer = Annotated[Principal, Depends(require(actions.ALERT_WRITE))]
DestinationReader = Annotated[Principal, Depends(require(actions.ALERT_DESTINATION_READ))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        400: "The cursor is not valid.",
        401: "Missing or invalid credential.",
        403: "The caller's role may not use this route (API keys never may).",
        404: "Not found, or not visible to this caller (never reveals other workspaces).",
        409: (
            "A name is taken, a workspace limit is reached, or a delivery holds the rows the "
            "change needs (`ALERT_BUSY`; retry)."
        ),
        422: "The body is invalid; `details` names the field or reason, never a value.",
        429: "Too many alert changes; see `Retry-After`.",
        504: "The preview took too long.",
    }.items()
}


def _service(request: Request) -> AlertsService:
    service: AlertsService = request.app.state.alerts
    return service


@router.post(
    "/v1/alerts/destinations",
    status_code=201,
    response_model=s.DestinationCreated,
    responses=_ERRORS,
    summary="Create an alert destination",
    description=(
        "A named target of type `webhook`, `slack` or `email`. The target is validated (https "
        "only, no credentials in the URL, no private or internal address literals, no internal "
        "host names; nothing is resolved now, delivery re-checks the address) and stored "
        "encrypted. A webhook's `signing_secret` is returned in this response only. `email` needs "
        "the operator to configure SMTP (`EMAIL_NOT_CONFIGURED` otherwise). Audit: "
        "`alert.destination.create`."
    ),
)
async def create_destination(
    body: s.DestinationCreate, request: Request, principal: Writer
) -> s.DestinationCreated:
    return await _service(request).create_destination(principal, body)


@router.get(
    "/v1/alerts/destinations",
    response_model=s.DestinationList,
    responses=_ERRORS,
    summary="List alert destinations",
    description=(
        "Masked targets and health, never a URL, a recipient or a secret. Owners and admins."
    ),
)
async def list_destinations(request: Request, principal: DestinationReader) -> s.DestinationList:
    return await _service(request).list_destinations(principal)


@router.get(
    "/v1/alerts/destinations/{destination_id}",
    response_model=s.DestinationOut,
    responses=_ERRORS,
    summary="Read an alert destination",
)
async def get_destination(
    destination_id: str, request: Request, principal: DestinationReader
) -> s.DestinationOut:
    return await _service(request).get_destination(principal, destination_id)


@router.patch(
    "/v1/alerts/destinations/{destination_id}",
    response_model=s.DestinationCreated,
    responses=_ERRORS,
    summary="Change an alert destination",
    description=(
        "Rename, replace the target (re-validated), pause or resume, or rotate the signing "
        "secret (returned once, in `signing_secret`). Audit: `alert.destination.update`, "
        "`.pause`, `.resume`, `.rotate_secret`."
    ),
)
async def patch_destination(
    destination_id: str, body: s.DestinationPatch, request: Request, principal: Writer
) -> s.DestinationCreated:
    return await _service(request).update_destination(principal, destination_id, body)


@router.post(
    "/v1/alerts/destinations/{destination_id}/test",
    response_model=s.DestinationTestResult,
    responses=_ERRORS,
    summary="Send a test alert to a destination",
    description=(
        "Sends a fixed test alert (`kind: test`, no project, agent or captured content) through "
        "the same SSRF-safe sender as real alerts, synchronously with the short delivery "
        "timeout, and returns `{ok, error_code}`; the response never carries what the target "
        "answered. A test does not change the destination's failure count or state and is not "
        "listed in the history. Owners and admins; rate limited. Audit: "
        "`alert.destination.test` (ok and the fixed code only)."
    ),
)
async def test_destination(
    destination_id: str, request: Request, principal: Writer
) -> s.DestinationTestResult:
    return await _service(request).test_destination(principal, destination_id)


@router.delete(
    "/v1/alerts/destinations/{destination_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Delete an alert destination",
    description=(
        "Alerts still waiting for it are closed as skipped (`destination_deleted`); history "
        "rows keep their place with the destination cleared; rules left with no destination are "
        "switched off. Audit: `alert.destination.delete`."
    ),
)
async def delete_destination(destination_id: str, request: Request, principal: Writer) -> Response:
    await _service(request).delete_destination(principal, destination_id)
    return Response(status_code=204)


@router.post(
    "/v1/alerts/rules",
    status_code=201,
    response_model=s.RuleOut,
    responses=_ERRORS,
    summary="Create an alert rule",
    description=(
        "One trigger, its filters, 1 to 10 destinations and a cooldown. Filters allowed per "
        "trigger: `approval_requested` (agent); `approval_expiring` (agent, "
        "`expiring_lead_seconds` 60..86400, required); `security_finding` (agent, "
        "`min_severity`, required); `run_failed` (agent, `run_statuses`); `spend_over` (agent, "
        "`threshold_usd` > 0, required; fires once per rule per UTC day when the day's spend "
        "exceeds it). A project or destination that is not the workspace's is a 404. Audit: "
        "`alert.rule.create`."
    ),
)
async def create_rule(body: s.RuleCreate, request: Request, principal: Writer) -> s.RuleOut:
    return await _service(request).create_rule(principal, body)


@router.get(
    "/v1/alerts/rules",
    response_model=s.RuleList,
    responses=_ERRORS,
    summary="List alert rules",
    description=(
        "Every role that reads runs sees the rules. `destinations` is filled for owners and "
        "admins only; others get `destination_count`."
    ),
)
async def list_rules(request: Request, principal: Reader) -> s.RuleList:
    return await _service(request).list_rules(principal)


@router.get(
    "/v1/alerts/rules/{rule_id}",
    response_model=s.RuleOut,
    responses=_ERRORS,
    summary="Read an alert rule",
)
async def get_rule(rule_id: str, request: Request, principal: Reader) -> s.RuleOut:
    return await _service(request).get_rule(principal, rule_id)


@router.patch(
    "/v1/alerts/rules/{rule_id}",
    response_model=s.RuleOut,
    responses=_ERRORS,
    summary="Change or mute an alert rule",
    description=(
        "Fields left out stay as they are; `filters` and `destination_ids` replace the stored "
        "values. `enabled: false` mutes the rule. The trigger cannot change. Audit: "
        "`alert.rule.update` with the names of the changed fields."
    ),
)
async def patch_rule(
    rule_id: str, body: s.RulePatch, request: Request, principal: Writer
) -> s.RuleOut:
    return await _service(request).update_rule(principal, rule_id, body)


@router.delete(
    "/v1/alerts/rules/{rule_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Delete an alert rule",
    description="Waiting alerts of the rule are closed as skipped (`rule_deleted`); history stays.",
)
async def delete_rule(rule_id: str, request: Request, principal: Writer) -> Response:
    await _service(request).delete_rule(principal, rule_id)
    return Response(status_code=204)


@router.get(
    "/v1/alerts/rules/{rule_id}/preview",
    response_model=s.RulePreview,
    responses=_ERRORS,
    summary="How often the rule would have fired",
    description=(
        "Counts only, over the last `days` (1 to 30), computed from recorded findings, "
        "approvals, runs and daily spend before cooldown. Nothing is delivered or stored."
    ),
)
async def preview_rule(
    rule_id: str,
    request: Request,
    principal: Reader,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
) -> s.RulePreview:
    return await _service(request).preview_rule(principal, rule_id, days)


@router.get(
    "/v1/alerts/notifications",
    response_model=s.NotificationPage,
    responses=_ERRORS,
    summary="List the alert history",
    description=(
        "Newest first, keyset-paged. Each row is one alert for one destination: ids, rule and "
        "destination names, trigger, severity, status, attempts and a fixed error code; never a "
        "payload body or a response body. Filtering by `destination_id` and the destination's id "
        "and name are for owners and admins; other roles see the destination type only."
    ),
)
async def list_notifications(
    request: Request,
    principal: Reader,
    rule_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.ALERT_RULE))] = None,
    destination_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.DESTINATION))] = None,
    status: Annotated[s.NotificationStatusName | None, Query()] = None,
    trigger: Annotated[s.TriggerName | None, Query()] = None,
    since: Annotated[datetime | None, Query(description="Created at or after.")] = None,
    until: Annotated[datetime | None, Query(description="Created at or before.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> s.NotificationPage:
    for name, value in (("since", since), ("until", until)):
        if value is not None and value.tzinfo is None:
            raise AppError(
                "INVALID_TIME",
                f"`{name}` needs a timezone, for example 2026-01-01T00:00:00Z.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
            )
    if destination_id is not None:
        await authorize_audited(request, principal, actions.ALERT_DESTINATION_READ)
    return await _service(request).list_notifications(
        principal,
        rule_id=rule_id,
        destination_id=destination_id,
        status=status,
        trigger=trigger,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )
