"""The approvers' lists and the audit trail of the agent's own transitions."""

from abb_event_schema.ids import IdKind, new_id

from tests.api_fixtures import Api
from tests.approvals.helpers import approve, ask, decide, intent, member, publish
from tests.test_audit import rows


async def test_lists_filter_by_state_and_project_and_page_with_a_cursor(api: Api) -> None:
    await publish(api)
    owner = await member(api, "owner@acme.test", "OWNER")
    made = [(await ask(api, arguments={"i": i}))[1] for i in range(5)]
    await approve(api, made[0])  # one decided, four waiting
    pending = (await api.client.get("/v1/approvals?state=pending", headers=owner)).json()
    assert sorted(i["id"] for i in pending["items"]) == sorted(made[1:])
    decided = (await api.client.get("/v1/approvals?state=decided", headers=owner)).json()
    assert [i["id"] for i in decided["items"]] == [made[0]]
    assert decided["items"][0]["state"] == "APPROVED" and decided["items"][0]["decided_by"]
    everything = (await api.client.get("/v1/approvals?state=all", headers=owner)).json()
    assert len(everything["items"]) == 5

    first = (await api.client.get("/v1/approvals?state=all&limit=2", headers=owner)).json()
    second = (
        await api.client.get(
            f"/v1/approvals?state=all&limit=2&cursor={first['next_cursor']}", headers=owner
        )
    ).json()
    last = (
        await api.client.get(
            f"/v1/approvals?state=all&limit=2&cursor={second['next_cursor']}", headers=owner
        )
    ).json()
    seen = [i["id"] for page in (first, second, last) for i in page["items"]]
    assert len(seen) == 5 and len(set(seen)) == 5 and last["next_cursor"] is None
    assert (await api.client.get("/v1/approvals?cursor=garbage", headers=owner)).status_code == 400

    alpha, beta = api.tenant.projects["alpha"], api.tenant.projects["beta"]
    in_alpha = await api.client.get(f"/v1/approvals?state=all&project_id={alpha}", headers=owner)
    in_beta = await api.client.get(f"/v1/approvals?state=all&project_id={beta}", headers=owner)
    assert len(in_alpha.json()["items"]) == 5 and in_beta.json()["items"] == []
    foreign = await api.client.get(
        f"/v1/approvals?project_id={api.other.projects['p']}", headers=owner
    )
    assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    assert (await api.client.get("/v1/approvals?state=bogus", headers=owner)).status_code == 422


async def test_the_detail_shows_what_the_approver_needs_and_the_summary_the_time_left(
    api: Api,
) -> None:
    await publish(api)
    owner = await member(api, "owner@acme.test", "OWNER")
    body, approval_id = await ask(api, arguments={"remote": "origin", "force": True})
    detail = (await api.client.get(f"/v1/approvals/{approval_id}", headers=owner)).json()
    assert detail["capability"] == "github.push" and detail["resource"] == "main"
    assert detail["arguments"] == {"remote": "origin", "force": True}
    assert detail["policy_id"] == "gate-push" and detail["policy_version"] == 1
    assert detail["risk_class"] == "R3" and detail["approver_role"] is None
    assert detail["run_id"] == body["run_id"] and detail["agent_id"] == "coding-agent"
    assert detail["project_id"] == api.tenant.projects["alpha"]
    assert detail["seconds_left"] == 900 and detail["on_expiry"] == "deny"
    assert detail["consumed"] is False and detail["comment"] is None
    assert not {"intent_hash", "hash", "requested_by_key_id"} & set(detail)


async def test_withdrawing_is_audited_as_the_key_and_a_plain_allow_leaves_no_audit_row(
    api: Api,
) -> None:
    await publish(api)
    allowed = await decide(api, intent(capability="x.y", resource="z"))
    assert allowed.json()["decision"] == "allow"
    _, approval_id = await ask(api)
    assert (
        await api.client.delete(f"/v1/approvals/{approval_id}", headers=api.headers())
    ).status_code == 204
    again = await api.client.delete(f"/v1/approvals/{approval_id}", headers=api.headers())
    assert again.status_code == 409 and again.json()["error"]["code"] == "APPROVAL_NOT_REQUESTED"
    unknown = await api.client.delete(
        f"/v1/approvals/{new_id(IdKind.APPROVAL)}", headers=api.headers()
    )
    assert unknown.status_code == 404
    ours = [r for r in await rows(api) if r.action.startswith(("approval.", "policy.decide"))]
    assert [(r.action, r.actor_kind, r.resource_id) for r in ours] == [
        ("approval.cancel", "api_key", approval_id)
    ]
