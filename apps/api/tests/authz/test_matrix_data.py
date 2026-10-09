"""The grant tables as data: a literal copy compared both ways, plus their invariants."""

from abb_api.auth import scopes
from abb_api.authz import actions as a
from abb_api.authz.matrix import (
    ALL_ROLES,
    ROLE_GRANTS,
    SCOPE_ACTIONS,
    Own,
    role_actions,
    role_own_actions,
)
from abb_api.db.tables import ROLES
from tests.authz.registry import CASES

KEY_BASE = {"workspace.read", "project.read"}

# Literal expected grants. Deliberately not imported from the code under test.
EXPECTED_SCOPE_ACTIONS: dict[str, set[str]] = {
    "events:write": KEY_BASE | {"event.write", "run.write"},
    "runs:read": KEY_BASE
    | {"run.read", "analytics.read", "payload.read", "artifact.read", "pricing.read"},
    "artifacts:write": KEY_BASE | {"artifact.write"},
    "policy:check": KEY_BASE,
}

_EVERYONE = {"workspace.read", "project.read", "member.read", "pricing.read"}
_CONTENT = {"payload.read", "artifact.read"}
EXPECTED_ROLE_ACTIONS: dict[str, set[str]] = {
    "OWNER": _EVERYONE | _CONTENT
    | {
        "run.read", "analytics.read", "project.write", "api_key.read", "api_key.create",
        "api_key.revoke", "member.write", "member.write_owner", "invite.read", "invite.write",
        "pricing.write", "audit.read", "billing.read",
    },
    "ADMIN": _EVERYONE | _CONTENT
    | {
        "run.read", "analytics.read", "project.write", "api_key.read", "api_key.create",
        "api_key.revoke", "member.write", "invite.read", "invite.write", "pricing.write",
        "audit.read", "billing.read",
    },
    "DEVELOPER": _EVERYONE | _CONTENT
    | {"run.read", "analytics.read", "api_key.read", "api_key.create"},
    "VIEWER": _EVERYONE | {"run.read", "analytics.read"},
    "SECURITY": _EVERYONE | _CONTENT
    | {"run.read", "analytics.read", "api_key.read", "api_key.revoke", "audit.read"},
    "BILLING": _EVERYONE | {"analytics.read", "pricing.write", "billing.read"},
}  # fmt: skip
EXPECTED_ROLE_OWN: dict[str, set[str]] = {"DEVELOPER": {"api_key.revoke"}}

ADMIN_ACTIONS = {
    "project.write", "member.read", "member.write", "member.write_owner", "invite.read",
    "invite.write", "api_key.read", "api_key.create", "api_key.revoke", "pricing.write",
    "audit.read", "billing.read",
}  # fmt: skip


def test_the_scope_grants_equal_the_literal_table_both_ways() -> None:
    assert {s: set(v) for s, v in SCOPE_ACTIONS.items()} == EXPECTED_SCOPE_ACTIONS


def test_the_role_grants_equal_the_literal_table_both_ways() -> None:
    assert set(ROLE_GRANTS) == set(EXPECTED_ROLE_ACTIONS)
    for role in ROLE_GRANTS:
        assert set(role_actions(role)) == EXPECTED_ROLE_ACTIONS[role], role
        assert set(role_own_actions(role)) == EXPECTED_ROLE_OWN.get(role, set()), role


def test_roles_match_the_database_roles_and_every_scope_is_known() -> None:
    assert set(ALL_ROLES) == set(ROLES) == set(ROLE_GRANTS)
    assert set(SCOPE_ACTIONS) == set(scopes.ALL_SCOPES)


def test_every_granted_action_exists_and_reserved_ones_are_granted_to_nobody() -> None:
    granted = {x for g in ROLE_GRANTS.values() for x in (role_actions_of(g))}
    granted |= {x for v in SCOPE_ACTIONS.values() for x in v}
    assert granted <= a.ALL_ACTIONS
    assert not granted & a.RESERVED_ACTIONS
    # Every non-reserved action is held by somebody, so the vocabulary has no dead names.
    assert a.ALL_ACTIONS - a.RESERVED_ACTIONS - granted == set()


def role_actions_of(grants: frozenset[str | Own]) -> set[str]:
    return {g.action if isinstance(g, Own) else g for g in grants}


def test_no_role_may_ingest() -> None:
    """People do not write telemetry; only project keys do (spec §92)."""
    for role, grants in ROLE_GRANTS.items():
        assert not role_actions_of(grants) & a.INGESTION_ACTIONS, role


def test_no_key_scope_may_administer_a_workspace() -> None:
    for scope, granted in SCOPE_ACTIONS.items():
        assert not set(granted) & ADMIN_ACTIONS, scope
        assert all(isinstance(x, str) for x in granted), "scopes have no resource-owner grants"


def test_own_grants_exist_only_for_roles_and_only_for_known_actions() -> None:
    for grants in ROLE_GRANTS.values():
        for grant in grants:
            if isinstance(grant, Own):
                assert grant.action in a.ALL_ACTIONS


def test_every_case_demands_a_real_action_that_some_actor_holds() -> None:
    held = {x for v in SCOPE_ACTIONS.values() for x in v}
    held |= {x for g in ROLE_GRANTS.values() for x in role_actions_of(g)}
    for case in CASES.values():
        assert case.action in a.ALL_ACTIONS
        assert case.action in held, f"{case.key}: nobody can ever call this route"


def test_payload_read_separates_content_from_metadata() -> None:
    """Decision 3: VIEWER and BILLING read metadata, never captured content."""
    for role in ("VIEWER", "BILLING"):
        assert "payload.read" not in role_actions(role)
        assert "artifact.read" not in role_actions(role)
    # Decision 4: BILLING reads prices and analytics but not runs.
    assert {"pricing.read", "analytics.read", "pricing.write"} <= role_actions("BILLING")
    assert "run.read" not in role_actions("BILLING")
