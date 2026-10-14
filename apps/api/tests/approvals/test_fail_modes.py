"""The fail-mode table an SDK caches (gate 4): rules with an explicit `on_error`, plus defaults."""

from tests.api_fixtures import Api
from tests.approvals.helpers import POLICY, member, publish

DEFAULTS = {
    "R0": "open",
    "R1": "open",
    "R2": "open",
    "R3": "closed",
    "R4": "closed",
    "unclassified": "open",
}
DOC = """\
version: 1
policies:
  - id: quiet-reads
    description: Reads are low risk.
    when: {capability: filesystem.read, risk_class: [R0, R1]}
    decision: allow
    on_error: open
  - id: no-force-push
    description: Never push without the service.
    when: {capability: github.*, resource: "**/main", attributes: {force: true}}
    decision: require_approval
    on_error: closed
  - id: plain
    description: No explicit mode.
    when: {capability: shell.execute}
    decision: allow
"""


async def get(api: Api, headers: dict[str, str], **params: str):  # type: ignore[no-untyped-def]
    return await api.client.get("/v1/policies/fail-modes", params=params, headers=headers)


async def test_nothing_published_gives_only_the_defaults(api: Api) -> None:
    out = (await get(api, api.headers("writer"))).json()
    assert out["source"] == "default" and out["policy_version"] == 0
    assert out["rules"] == [] and out["defaults"] == DEFAULTS


async def test_only_rules_with_an_explicit_on_error_are_listed_in_policy_order(api: Api) -> None:
    await publish(api, DOC)
    out = (await get(api, api.headers("writer"))).json()
    assert out["source"] == "project" and out["policy_version"] == 1
    assert out["project_id"] == api.tenant.projects["alpha"]
    assert out["rules"] == [
        {
            "rule_id": "quiet-reads",
            "capability": "filesystem.read",
            "resource": None,
            "risk_classes": ["R0", "R1"],
            "has_attribute_conditions": False,
            "on_error": "open",
        },
        {
            "rule_id": "no-force-push",
            "capability": "github.*",
            "resource": "**/main",
            "risk_classes": [],
            "has_attribute_conditions": True,
            "on_error": "closed",
        },
    ]
    assert out["defaults"] == DEFAULTS


async def test_the_workspace_base_applies_when_the_project_has_no_policy(api: Api) -> None:
    await publish(api, DOC, project=None)
    out = (await get(api, api.headers("writer"))).json()
    assert out["source"] == "workspace" and len(out["rules"]) == 2
    await publish(
        api, POLICY
    )  # the project's own policy replaces the base: no explicit modes but one
    out = (await get(api, api.headers("writer"))).json()
    assert out["source"] == "project" and [r["rule_id"] for r in out["rules"]] == ["gate-deploy"]


async def test_a_person_names_the_project_and_a_key_sees_only_its_own(api: Api) -> None:
    await publish(api, DOC)
    owner = await member(api, "owner@acme.test", "OWNER")
    alpha = api.tenant.projects["alpha"]
    assert len((await get(api, owner, project_id=alpha)).json()["rules"]) == 2
    assert (await get(api, owner)).json()["source"] == "default"  # the base, which is empty
    beta_view = (await get(api, api.headers("beta"))).json()
    assert beta_view["source"] == "default" and beta_view["rules"] == []
    assert (await get(api, api.headers("beta"), project_id=alpha)).status_code == 404
    other = await member(api, "owner@globex.test", "OWNER", "globex")
    assert (await get(api, other, project_id=alpha)).status_code == 404


async def test_viewers_billing_and_unscoped_keys_cannot_read_it(api: Api) -> None:
    for role in ("VIEWER", "BILLING"):
        headers = await member(api, f"{role.lower()}@acme.test", role)
        assert (await get(api, headers)).status_code == 403
    assert (await get(api, api.headers("reader"))).status_code == 403
    assert (await get(api, api.headers("none"))).status_code == 401
