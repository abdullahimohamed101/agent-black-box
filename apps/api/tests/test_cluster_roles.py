"""Every cluster-global role a migration creates must be provisioned by the test harness (xdist)."""

import re

from tests.conftest import API_DIR, CLUSTER_ROLES


def test_every_role_a_migration_creates_is_provisioned_by_the_harness() -> None:
    created: set[str] = set()
    for path in (API_DIR / "migrations" / "versions").glob("*.py"):
        text = path.read_text()
        if "CREATE ROLE" not in text:
            continue
        named = set(re.findall(r"CREATE ROLE (\w+)", text)) - {"{ROLE}"}
        named |= set(re.findall(r'^ROLE = "(\w+)"', text, flags=re.MULTILINE))
        assert named, f"{path.name} creates a role the harness cannot name"
        created |= named
    assert created <= set(CLUSTER_ROLES), sorted(created - set(CLUSTER_ROLES))
