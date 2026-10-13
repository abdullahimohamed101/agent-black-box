"""Which signed-in role may decide which approval (ADR-141): a literal table, both ways."""

import pytest

from abb_api.approvals.roles import SATISFIED_BY, may_decide

ROLES = ("OWNER", "ADMIN", "SECURITY", "DEVELOPER", "VIEWER", "BILLING", None, "ROOT")

#              OWNER ADMIN SECURITY DEVELOPER VIEWER BILLING none unknown
EXPECTED = {
    None: "yes   yes   no       no        no     no      no   no",
    "OWNER": "yes   no    no       no        no     no      no   no",
    "ADMIN": "yes   yes   no       no        no     no      no   no",
    "SECURITY": "yes   yes   yes      no        no     no      no   no",
    "DEVELOPER": "yes   yes   yes      yes       no     no      no   no",
}


def test_the_ladder_matches_the_literal_table_for_every_role() -> None:
    assert set(EXPECTED) == set(SATISFIED_BY)
    for approver, row in EXPECTED.items():
        cells = row.split()
        assert len(cells) == len(ROLES)
        for role, cell in zip(ROLES, cells, strict=True):
            assert may_decide(approver, role) == (cell == "yes"), (approver, role)


@pytest.mark.parametrize("approver", [None, "OWNER", "ADMIN", "SECURITY", "DEVELOPER", "STRANGER"])
def test_viewer_and_billing_never_decide(approver: str | None) -> None:
    assert not may_decide(approver, "VIEWER") and not may_decide(approver, "BILLING")


def test_an_unknown_approver_role_is_decided_by_nobody() -> None:
    assert not any(may_decide("STRANGER", role) for role in ROLES)
