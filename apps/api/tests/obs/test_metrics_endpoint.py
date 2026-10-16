"""GET /metrics: off without a token, bearer-only, rate limited, never leaking the token."""

import logging

import httpx
import pytest
from pydantic import ValidationError

from abb_api.core.config import Settings
from abb_api.main import create_app
from abb_api.obs.catalogue import build_observability
from tests.obs.helpers import PLACEHOLDER_DB, TOKEN, client_for, make_app, scrape
from tests.obs.parser import sample_value


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_without_a_token_the_route_does_not_exist() -> None:
    async with client_for(make_app(token=None)) as client:
        metrics = await client.get("/metrics", headers=bearer(TOKEN))
        unknown = await client.get("/no-such-path", headers=bearer(TOKEN))
    assert metrics.status_code == 404
    assert _without_request_id(metrics) == _without_request_id(
        unknown
    )  # indistinguishable from any unknown path
    assert metrics.json()["error"]["code"] == unknown.json()["error"]["code"]


async def test_wrong_and_missing_tokens_get_the_same_401() -> None:
    async with client_for(make_app()) as client:
        missing = await client.get("/metrics")
        wrong = await client.get("/metrics", headers=bearer("x" * 40))
        shorter = await client.get("/metrics", headers=bearer(TOKEN[:-1]))
        longer = await client.get("/metrics", headers=bearer(TOKEN + "x"))
        basic = await client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"})
        empty = await client.get("/metrics", headers={"Authorization": "Bearer "})
    responses = [missing, wrong, shorter, longer, basic, empty]
    assert {r.status_code for r in responses} == {401}
    bodies = {_without_request_id(r) for r in responses}
    assert len(bodies) == 1
    assert missing.json()["error"]["category"] == "AUTHENTICATION"
    assert {r.headers["www-authenticate"] for r in responses} == {"Bearer"}


def _without_request_id(response: httpx.Response) -> str:
    body = response.json()
    body["error"]["request_id"] = None
    return str(body)


async def test_the_right_token_gets_the_exposition() -> None:
    async with client_for(make_app()) as client:
        response = await client.get("/metrics", headers=bearer(TOKEN))
        lowercase = await client.get("/metrics", headers={"Authorization": f"bearer {TOKEN}"})
    assert response.status_code == 200 == lowercase.status_code
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]
    assert "# TYPE http_requests_total counter" in response.text


async def test_the_exposition_counts_its_own_traffic_by_template() -> None:
    obs = build_observability()
    async with client_for(make_app(observability=obs)) as client:
        await client.get("/healthz")
        await client.get("/metrics")  # a 401
        families = await scrape(client)
    assert (
        sample_value(families, "http_requests_total", method="GET", route="/healthz", status="200")
        == 1
    )
    assert (
        sample_value(families, "http_requests_total", method="GET", route="/metrics", status="401")
        == 1
    )


async def test_it_is_hidden_from_the_openapi_document_and_the_docs() -> None:
    app = make_app()
    assert "/metrics" not in app.openapi()["paths"]
    async with client_for(app) as client:
        assert "/metrics" not in (await client.get("/openapi.json")).text


async def test_the_endpoint_is_rate_limited_before_the_token_is_checked() -> None:
    async with client_for(make_app(metrics_requests_per_minute=5)) as client:
        codes = [
            (await client.get("/metrics", headers=bearer(TOKEN))).status_code for _ in range(5)
        ]
        limited = await client.get("/metrics", headers=bearer(TOKEN))
        guess = await client.get("/metrics", headers=bearer("guess"))
    assert codes == [200] * 5
    assert limited.status_code == 429 == guess.status_code
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) >= 1


async def test_only_get_is_served() -> None:
    async with client_for(make_app()) as client:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            response = await client.request(method, "/metrics", headers=bearer(TOKEN))
            assert response.status_code == 405


async def test_the_token_never_appears_in_logs_responses_or_settings_repr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = Settings(
        database_url=PLACEHOLDER_DB,
        environment="test",
        metrics_token=TOKEN,  # type: ignore[arg-type]
    )
    app = create_app(settings, observability=build_observability())
    async with client_for(app) as client:
        responses = [
            await client.get("/metrics", headers=bearer(TOKEN)),
            await client.get("/metrics", headers=bearer("wrong-" + TOKEN)),
            await client.get("/metrics?token=" + TOKEN),
            await client.get("/healthz", headers=bearer(TOKEN)),
        ]
    texts = [r.text + str(dict(r.headers)) for r in responses]
    texts.append(repr(settings))
    texts.append(settings.model_dump_json())
    texts.append(caplog.text)
    texts.extend(str(record.__dict__) for record in caplog.records)
    assert all(TOKEN not in text for text in texts)
    assert all("wrong-" + TOKEN not in text for text in texts)


@pytest.mark.parametrize("environment", ["production", "staging", "development", "test"])
def test_a_short_token_is_refused_at_startup_in_every_environment(environment: str) -> None:
    short = "s" * 23
    with pytest.raises(ValidationError) as caught:
        Settings(
            database_url=PLACEHOLDER_DB,
            environment=environment,  # type: ignore[arg-type]
            metrics_token=short,  # type: ignore[arg-type]
        )
    assert short not in str(caught.value)  # a refused token is not echoed into the log either
    assert "at least 24" in str(caught.value)
    Settings(
        database_url=PLACEHOLDER_DB,
        environment=environment,  # type: ignore[arg-type]
        metrics_token="s" * 24,  # type: ignore[arg-type]
    )


def test_a_blank_token_means_unset() -> None:
    for blank in ("", "   "):
        settings = Settings(database_url=PLACEHOLDER_DB, metrics_token=blank)  # type: ignore[arg-type]
        assert settings.metrics_token is None


def test_the_worker_port_needs_the_token_and_a_real_bind_address() -> None:
    with pytest.raises(ValidationError, match="needs metrics_token"):
        Settings(database_url=PLACEHOLDER_DB, worker_metrics_port=9100)
    with pytest.raises(ValidationError, match="IP address"):
        Settings(
            database_url=PLACEHOLDER_DB,
            metrics_token=TOKEN,  # type: ignore[arg-type]
            worker_metrics_port=9100,
            worker_metrics_bind="example.com",
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url=PLACEHOLDER_DB,
            metrics_token=TOKEN,  # type: ignore[arg-type]
            worker_metrics_port=70000,
        )
    ok = Settings(database_url=PLACEHOLDER_DB, worker_metrics_port="")  # type: ignore[arg-type]
    assert ok.worker_metrics_port is None and ok.worker_metrics_bind == "127.0.0.1"
