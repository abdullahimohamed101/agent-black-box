"""After hostile traffic across every instrumented family: bounded series, closed vocabularies,
and not one tenant, key, address or user string anywhere in the exposition."""

import json
import random
import string
import uuid

from abb_api.obs.catalogue import Observability
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.helpers import TOKEN
from tests.obs.parser import parse
from tests.test_auth_throttle import probe, unknown_key, wrong_secret

CANARIES = [
    "CANARY-AGENT-7",
    "canary.event.type",
    "CANARY-ATTR-VALUE",
    "CANARY-ARTIFACT-NAME",
    "198.51.100.99",
    "CANARY-UA",
    "canary-project-slug",
    "CANARYREQUEST",
]
SERIES_CEILING = 900  # the whole registry, after thousands of hostile requests


async def hostile_traffic(api: Api) -> set[str]:
    """Drive every instrumented path with junk and canaries. Returns strings that must not leak."""
    rng = random.Random(11)
    leaks: set[str] = set()
    headers = {
        "x-forwarded-for": "198.51.100.99",
        "user-agent": "CANARY-UA",
        "x-request-id": "CANARYREQUEST",
    }
    # Ingestion: invalid, reserved, oversized, valid, wrong media type, junk JSON, canary fields.
    for i in range(60):
        run = make_run_ids()
        junk = "".join(rng.choices(string.ascii_letters, k=12))
        events: list[object] = [
            wire_event(run, 1, agent_id="CANARY-AGENT-7", attributes={"k": "CANARY-ATTR-VALUE"}),
            wire_event(run, 2, event_type="canary.event.type"),
            wire_event(run, 3, event_type="policy.decision"),
            wire_event(run, 4, occurred_at=junk),
            {"event_id": junk, "run_id": junk, "canary": "CANARY-ATTR-VALUE"},
            junk,
        ]
        body = json.dumps({"events": events, "batch_id": junk[:8]}).encode()
        await api.client.post(
            "/v1/events/batch", content=body, headers={**api.headers(), **headers}
        )
        await api.client.post(
            "/v1/events", content=body[: i + 5], headers={**api.headers(), **headers}
        )
    await api.client.post(
        "/v1/events/batch", content=b"x", headers={**api.headers(), "content-type": "image/png"}
    )
    # Failed authentication with many distinct, well-formed key ids.
    for _ in range(80):
        token = unknown_key()
        leaks.update({token, token.split(".")[0], token.removeprefix("abb_live_").split(".")[0]})
        await probe(api, token, "198.51.100.99")
    real = wrong_secret(api.tokens["writer"])
    leaks.add(real.split(".")[0])
    for _ in range(5):
        await probe(api, real, "198.51.100.99")
    # Search with garbage; sessions endpoints with garbage; artifacts with canary names.
    for _ in range(20):
        junk = "".join(rng.choices(string.printable, k=30))
        await api.get("/v1/runs", token="reader", q=junk)
        await api.client.delete(f"/v1/auth/sessions/{uuid.uuid4()}", headers=headers)
    await api.client.put(
        f"/v1/artifacts/art_{'0' * 26}",
        params={"run_id": make_run_ids()["run_id"], "name": "CANARY-ARTIFACT-NAME"},
        content=b"x",
        headers={**api.headers(), "content-type": "text/plain"},
    )
    await api.drain()
    leaks.update(api.tokens.values())
    leaks.update(t.split(".")[0] for t in api.tokens.values())
    leaks.update({api.tenant.workspace_id, api.other.workspace_id, *api.tenant.projects.values()})
    return leaks


async def test_series_stay_bounded_vocabularies_stay_closed_and_nothing_leaks(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    leaks = await hostile_traffic(api)
    scraped = await api.client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert scraped.status_code == 200
    text = scraped.text
    families = parse(text)

    assert obs.registry.series_total() < SERIES_CEILING
    for name, metric in obs.registry.metrics.items():
        assert metric.series_count() <= metric._max_series, name
        for label in metric.labels:
            seen = {
                labels[label.name]
                for _, labels, _ in families[name].samples
                if label.name in labels
            }
            if label.values is not None:
                assert seen <= label.values | {"other"}, (name, label.name, seen - label.values)
            else:
                assert len(seen) <= label.max_values + 1, (name, label.name, len(seen))

    lowered = text.lower()
    for canary in CANARIES:
        assert canary.lower() not in lowered, f"{canary!r} reached /metrics"
    for secret in leaks:
        assert secret not in text, f"{secret!r} reached /metrics"
    assert TOKEN not in text
    # The traffic really exercised the families (otherwise the test proves nothing).
    moved = {name for name, family in families.items() if any(v for _, _, v in family.samples)}
    for name in (
        "telemetry_events_received_total",
        "telemetry_events_rejected_total",
        "ingest_acks_total",
        "auth_throttle_blocks_total",
        "worker_jobs_total",
        "http_requests_total",
    ):
        assert name in moved, name
