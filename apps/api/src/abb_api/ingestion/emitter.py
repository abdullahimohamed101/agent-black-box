"""The emitter name only the server may use (ADR-141).

Events the policy service writes carry `sdk.name = "abb-policy-service"`. Ingestion refuses that
name from clients, so the name on an event says who wrote it and cannot be forged by an SDK.
"""

SERVER_EMITTER = "abb-policy-service"
EMITTER_RESERVED_CODE = "emitter_reserved"
RESERVED_TYPE_CODE = "RESERVED_EVENT_TYPE"
RESERVED_EVENT_TYPES = frozenset(
    {
        "policy.decision",
        "policy.action.blocked",
        "approval.requested",
        "approval.granted",
        "approval.denied",
        "approval.consumed",
        "approval.expired",
        "approval.cancelled",
    }
)
