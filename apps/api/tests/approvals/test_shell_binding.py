"""P2-3 (Phase 14 review): an approval of a shell command binds to that exact command."""

from typing import Any

from tests.api_fixtures import Api
from tests.approvals.helpers import approval_row, approve, ask, consume, publish

SHELL_POLICY = """\
version: 1
policies:
  - id: shell-r3
    description: Risky shell commands need a person.
    when: {capability: shell.execute, risk_class: R3}
    decision: require_approval
"""


def shell(command: str, **overrides: Any) -> dict[str, Any]:
    return {
        "capability": "shell.execute",
        "resource": "git",
        "risk_class": "R3",
        "arguments": {"category": "NETWORK", "command": command},
        **overrides,
    }


async def test_an_approval_of_one_git_command_does_not_cover_another_of_the_same_shape(
    api: Api,
) -> None:
    await publish(api, SHELL_POLICY)
    body, approval_id = await ask(api, **shell("git push origin main"))
    assert (await approve(api, approval_id)).status_code == 200
    forced = {
        **body,
        "arguments": {"category": "NETWORK", "command": "git push --force origin main"},
    }
    response = await consume(api, approval_id, forced)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_INTENT_MISMATCH"
    assert (
        await consume(api, approval_id, body)
    ).status_code == 200  # the approved one still works


async def test_the_approver_sees_the_scrubbed_command(api: Api) -> None:
    from tests.approvals.helpers import member

    await publish(api, SHELL_POLICY)
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    _, approval_id = await ask(api, **shell(f"git push https://x:{secret}@example.com/r.git main"))
    headers = await member(api, "admin@acme.test", "ADMIN")
    detail = (await api.client.get(f"/v1/approvals/{approval_id}", headers=headers)).json()
    shown = detail["arguments"]["command"]
    assert "git push" in shown and secret not in shown
    assert secret not in str((await approval_row(api, approval_id))["arguments_scrubbed"])
