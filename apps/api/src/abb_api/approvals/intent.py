"""The action intent as it arrives, validated and hashed (ADR-141).

`IntentIn` is the request body of both the decision endpoint and consumption: the SDK sends the
same intent twice, and the server hashes what it received each time, so nothing the client says
about a hash is ever believed. Unknown fields are refused. Sizes are bounded here and again by the
canonicaliser in `abb_event_schema.intent`, which owns the byte-exact hash.
"""

import uuid
from typing import Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from abb_event_schema.intent import IntentError, intent_hash
from pydantic import BaseModel, ConfigDict, Field

from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import parse_public_id, public_id
from abb_api.policies.model import AttributeValue

CAPABILITY_PATTERN = r"^[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,5}$"
AGENT_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
MAX_ATTRIBUTES = 32
MAX_ATTRIBUTE_TEXT = 1024

RiskClass = Literal["R0", "R1", "R2", "R3", "R4"]


class IntentIn(BaseModel):
    """What an agent is about to do. The workspace and project come from the API key."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=id_pattern(IdKind.RUN), description="The run the action is in.")
    agent_id: str = Field(pattern=AGENT_PATTERN, description="The agent's slug, as in its events.")
    capability: str = Field(
        pattern=CAPABILITY_PATTERN,
        max_length=128,
        description="What kind of action, e.g. `shell.execute` or `github.merge`.",
    )
    resource: str = Field(
        default="",
        max_length=2048,
        description="What it acts on (an executable name, a path, a tool operation).",
    )
    risk_class: RiskClass | None = Field(default=None, description="The SDK classifier's class.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON arguments of the action: strings, integers, booleans, null, lists and "
            "objects, at most 8 deep and 512 values. Floats are refused (send decimals as "
            "strings). Top-level scalars are the attributes a policy rule can match."
        ),
    )
    trace_id: str | None = Field(
        default=None,
        pattern=id_pattern(IdKind.TRACE),
        description="The run's trace, used only if the run has no events yet. Not hashed.",
    )
    idempotency_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.:-]{1,64}$",
        description=(
            "Makes the events of an allow or deny decision idempotent across retries. Not hashed."
        ),
    )


def invalid_intent(code: str) -> AppError:
    return AppError(
        "INTENT_INVALID",
        "The action intent cannot be accepted.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"reason": code},
    )


def hash_of(principal: Principal, body: IntentIn) -> str:
    """SHA-256 of the canonical intent in the key's workspace and project (never logged)."""
    assert principal.project_id is not None
    try:
        return intent_hash(
            workspace_id=public_id(IdKind.WORKSPACE, principal.workspace_id),
            project_id=public_id(IdKind.PROJECT, principal.project_id),
            run_id=body.run_id,
            agent_id=body.agent_id,
            capability=body.capability,
            resource=body.resource,
            risk_class=body.risk_class,
            arguments=body.arguments,
        )
    except IntentError as error:
        raise invalid_intent(error.code) from None


def run_uuid(body: IntentIn) -> uuid.UUID:
    parsed = parse_public_id(IdKind.RUN, body.run_id)
    assert parsed is not None  # the pattern admits only well-formed ids
    return parsed


def attributes_of(body: IntentIn) -> dict[str, AttributeValue]:
    """The top-level scalar arguments, which a policy rule's `attributes` can match. Lists and
    objects are never matched; text is bounded so a rule comparison is cheap."""
    found: dict[str, AttributeValue] = {}
    for key in sorted(body.arguments):
        value = body.arguments[key]
        if isinstance(value, bool | int) or (
            isinstance(value, str) and len(value) <= MAX_ATTRIBUTE_TEXT
        ):
            found[key] = value
        if len(found) >= MAX_ATTRIBUTES:
            break
    return found
