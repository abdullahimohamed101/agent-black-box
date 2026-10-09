"""`authorize()` on its own: roles, scopes and owner-conditional grants."""

import uuid

import pytest

from abb_api.authz import actions as a
from abb_api.authz.matrix import role_actions, role_own_actions, scope_actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import PermissionDenied, authorize


class Resource:
    def __init__(self, created_by: uuid.UUID | None) -> None:
        self.created_by = created_by


def user(role: str, user_id: uuid.UUID | None = None) -> Principal:
    user_id = user_id or uuid.uuid4()
    return Principal(
        "user", uuid.uuid4(), None, role_actions(role), f"user:{user_id}",
        own_actions=role_own_actions(role), user_id=user_id,
    )  # fmt: skip


def test_a_held_action_passes_and_a_missing_one_names_the_permission() -> None:
    viewer = user("VIEWER")
    authorize(viewer, a.RUN_READ)
    with pytest.raises(PermissionDenied) as denied:
        authorize(viewer, a.API_KEY_CREATE)
    assert denied.value.status_code == 403 and denied.value.code == "PERMISSION_DENIED"
    assert denied.value.details == {"required_permission": "api_key.create"}


def test_owner_conditional_grants_need_the_creator_to_match() -> None:
    me = uuid.uuid4()
    developer = user("DEVELOPER", me)
    authorize(developer, a.API_KEY_REVOKE, Resource(me))
    for resource in (Resource(uuid.uuid4()), Resource(None), None):
        with pytest.raises(PermissionDenied):
            authorize(developer, a.API_KEY_REVOKE, resource)
    authorize(user("ADMIN"), a.API_KEY_REVOKE, Resource(uuid.uuid4()))


def test_a_key_is_denied_with_the_scope_that_would_grant_the_action() -> None:
    key = Principal("api_key", uuid.uuid4(), None, scope_actions(frozenset({"runs:read"})), "key:k")
    with pytest.raises(PermissionDenied) as denied:
        authorize(key, a.MEMBER_READ)
    assert denied.value.code == "INSUFFICIENT_SCOPE"
    assert "required_scope" not in (denied.value.details or {})  # no scope grants it
    with pytest.raises(PermissionDenied) as denied:
        authorize(key, a.EVENT_WRITE)
    assert (denied.value.details or {})["required_scope"] == "events:write"


def test_an_unknown_role_holds_nothing() -> None:
    nobody = user("ROOT")
    with pytest.raises(PermissionDenied):
        authorize(nobody, a.WORKSPACE_READ)
