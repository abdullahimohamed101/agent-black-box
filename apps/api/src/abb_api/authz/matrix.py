"""Who may do what, as data (spec §91, ADR-061).

`ROLE_GRANTS` is for people (workspace roles), `SCOPE_ACTIONS` for API keys. The tests keep a
second, literal copy of the expected outcomes and compare both ways, so changing a grant here is a
deliberate two-place edit.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from abb_api.auth import scopes
from abb_api.authz import actions as a


@dataclass(frozen=True)
class Own:
    """A grant that holds only for resources the actor created (`resource.created_by == user`)."""

    action: str


OWNER: Final = "OWNER"
ADMIN: Final = "ADMIN"
DEVELOPER: Final = "DEVELOPER"
VIEWER: Final = "VIEWER"
SECURITY: Final = "SECURITY"
BILLING: Final = "BILLING"
ALL_ROLES: Final = (OWNER, ADMIN, DEVELOPER, VIEWER, SECURITY, BILLING)

_EVERYONE = frozenset(
    {a.WORKSPACE_READ, a.PROJECT_READ, a.MEMBER_READ, a.PRICING_READ, a.RETENTION_READ}
)
_READ_RUNS = frozenset({a.RUN_READ, a.EVALUATION_READ, a.EXPERIMENT_READ})
# People who may start evaluators, record verdicts and shape experiments (Phase 11, decision 1).
_EVALUATE = frozenset({a.EVALUATION_RUN, a.EVALUATION_LABEL, a.EVALUATOR_WRITE, a.EXPERIMENT_WRITE})
_CONTENT = frozenset({a.PAYLOAD_READ, a.ARTIFACT_READ})
# People who read policies: those who build agents and review their safety (Phase 14, ADR-140).
_POLICY_READ = frozenset({a.POLICY_READ, a.POLICY_FAIL_MODES})
# People who read payloads read approvals and may be asked to decide them (Phase 14, ADR-141);
# the rule's `approver_role` and the requester check narrow who may decide a particular one.
_APPROVALS = frozenset({a.APPROVAL_READ, a.APPROVAL_DECIDE})

# Alert rules and the delivery history are metadata: everyone who reads runs may see them.
# Destinations (masked targets, health) and all changes are for owners and admins (ADR-150).
_ALERTS_READ = frozenset({a.ALERT_READ})
_ALERTS_MANAGE = frozenset({a.ALERT_WRITE, a.ALERT_DESTINATION_READ})

ROLE_GRANTS: Final[Mapping[str, frozenset[str | Own]]] = {
    OWNER: _EVERYONE | _READ_RUNS | _CONTENT | _EVALUATE
    | {
        a.ANALYTICS_READ, a.PROJECT_WRITE, a.API_KEY_READ, a.API_KEY_CREATE, a.API_KEY_REVOKE,
        a.MEMBER_WRITE, a.MEMBER_WRITE_OWNER, a.INVITE_READ, a.INVITE_WRITE, a.PRICING_WRITE,
        a.AUDIT_READ, a.BILLING_READ, a.POLICY_READ, a.POLICY_WRITE, a.POLICY_FAIL_MODES,
        a.RETENTION_WRITE,
    }
    | _APPROVALS | _ALERTS_READ | _ALERTS_MANAGE,
    ADMIN: _EVERYONE | _READ_RUNS | _CONTENT | _EVALUATE
    | {
        a.ANALYTICS_READ, a.PROJECT_WRITE, a.API_KEY_READ, a.API_KEY_CREATE, a.API_KEY_REVOKE,
        a.MEMBER_WRITE, a.INVITE_READ, a.INVITE_WRITE, a.PRICING_WRITE, a.AUDIT_READ,
        a.BILLING_READ, a.POLICY_READ, a.POLICY_WRITE, a.POLICY_FAIL_MODES, a.RETENTION_WRITE,
    }
    | _APPROVALS | _ALERTS_READ | _ALERTS_MANAGE,
    DEVELOPER: _EVERYONE | _READ_RUNS | _CONTENT | _EVALUATE
    | _POLICY_READ | _APPROVALS | _ALERTS_READ
    | {a.ANALYTICS_READ, a.API_KEY_READ, a.API_KEY_CREATE, Own(a.API_KEY_REVOKE)},
    # Metadata only: captured content needs payload.read (decision 3).
    VIEWER: _EVERYONE | _READ_RUNS | _ALERTS_READ | {a.ANALYTICS_READ},
    SECURITY: _EVERYONE | _READ_RUNS | _CONTENT | _POLICY_READ | _APPROVALS | _ALERTS_READ
    | {a.ANALYTICS_READ, a.API_KEY_READ, a.API_KEY_REVOKE, a.AUDIT_READ},
    # Money only: analytics and prices, never runs or payloads (decision 4).
    BILLING: _EVERYONE | {a.ANALYTICS_READ, a.PRICING_WRITE, a.BILLING_READ},
}  # fmt: skip

_KEY_BASE = frozenset({a.WORKSPACE_READ, a.PROJECT_READ})
# What an agent's key may do with the policy service (Phase 14, ADR-141): ask, use its own
# approvals, read the fail-mode table. Both the ingestion scope and the dedicated `policy:check`
# scope grant it, so an existing SDK key works and a key that may only ask can be made.
_ASK = frozenset({a.POLICY_DECIDE, a.APPROVAL_USE, a.POLICY_FAIL_MODES})

# Keys never get member, invitation, key-management or audit actions (spec §92), and
# `runs:read` keeps implying payload and artifact reads for compatibility.
SCOPE_ACTIONS: Final[Mapping[str, frozenset[str]]] = {
    scopes.EVENTS_WRITE: _KEY_BASE | {a.EVENT_WRITE, a.RUN_WRITE} | _ASK,
    scopes.RUNS_READ: _KEY_BASE
    | {
        a.RUN_READ,
        a.ANALYTICS_READ,
        a.PAYLOAD_READ,
        a.ARTIFACT_READ,
        a.PRICING_READ,
        a.EVALUATION_READ,
        a.EXPERIMENT_READ,
    },
    scopes.ARTIFACTS_WRITE: _KEY_BASE | {a.ARTIFACT_WRITE},
    scopes.POLICY_CHECK: _KEY_BASE | _ASK,
}


def _plain(grants: frozenset[str | Own]) -> frozenset[str]:
    return frozenset(g for g in grants if isinstance(g, str))


def _own(grants: frozenset[str | Own]) -> frozenset[str]:
    return frozenset(g.action for g in grants if isinstance(g, Own))


def role_actions(role: str) -> frozenset[str]:
    """The actions a role holds outright. An unknown role holds nothing."""
    return _plain(ROLE_GRANTS.get(role, frozenset()))


def role_own_actions(role: str) -> frozenset[str]:
    """The actions a role holds only on resources the user created."""
    return _own(ROLE_GRANTS.get(role, frozenset()))


def scope_actions(granted: frozenset[str]) -> frozenset[str]:
    """The union of actions of a key's scopes (unknown scopes grant nothing)."""
    result: frozenset[str] = frozenset()
    for scope in granted:
        result |= SCOPE_ACTIONS.get(scope, frozenset())
    return result


def scope_for(action: str) -> str | None:
    """The scope that grants `action` (first in a fixed order), for error messages."""
    for scope in (
        scopes.POLICY_CHECK,
        scopes.EVENTS_WRITE,
        scopes.RUNS_READ,
        scopes.ARTIFACTS_WRITE,
    ):
        if action in SCOPE_ACTIONS[scope] and action not in _KEY_BASE:
            return scope
    return None
