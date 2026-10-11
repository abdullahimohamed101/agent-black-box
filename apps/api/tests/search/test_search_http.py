"""`GET /v1/runs?q=` end to end (AC-3 HTTP half, AC-7 statement counts): the public contract."""

import pytest

from tests.api_fixtures import Api
from tests.search.http_helpers import item_ids, mentioning, seed_named, statements
from tests.search.test_search_api import SEEDS

pytestmark = pytest.mark.usefixtures("database_url")

READER = "wide_reader"


@pytest.fixture
async def ids(api: Api) -> dict[str, str]:
    found = await seed_named(api, SEEDS, drop_document_of="unindexed")
    created = await api.client.post(  # a queued run: created explicitly, no events, no document
        "/v1/runs", json={"name": "Queued job"}, headers=api.headers("writer")
    )
    assert created.status_code == 201, created.text
    found["queued"] = created.json()["id"]
    return found


def names(ids: dict[str, str], response: object) -> set[str]:
    by_id = {v: k for k, v in ids.items()}
    return {by_id[i] for i in item_ids(response)}  # type: ignore[arg-type]


async def test_a_search_returns_the_runs_the_query_names_with_why_and_facets(
    api: Api, ids: dict[str, str]
) -> None:
    response = await api.get("/v1/runs", token=READER, q="tool:github model:m*")
    assert names(ids, response) == {"fail"}
    body = response.json()
    item = body["items"][0]
    assert item["matched"] == {
        "tools": ["github"],
        "models": ["m1"],
        "event_types": [],
        "agents": [],
        "tags": [],
        "metadata_truncated": False,
        "content_truncated": False,  # a key with runs:read holds payload.read
    }
    assert body["facets"]["status"] == [{"value": "FAILED", "count": 1}]
    query = body["query"]
    assert query["content_allowed"] is True
    assert [(t["kind"], t["field"], t["value"], t["span"]) for t in query["terms"]] == [
        ("field", "tool", "github", [0, 11]),
        ("field", "model", "m", [12, 20]),
    ]
    assert query["terms"][1]["prefix"] is True
    assert query["unindexed"] == 2 and query["unindexed_capped"] is False  # queued + deleted doc
    assert {f["name"] for f in query["fields"]} >= {"status", "tool", "file", "command"}


async def test_the_echo_resolves_relative_times_and_names_negation(api: Api) -> None:
    body = (await api.get("/v1/runs", token=READER, q="-status:failed started:>=-24h")).json()
    first, second = body["query"]["terms"]
    assert first["negated"] is True and first["field"] == "status" and first["value"] == "FAILED"
    assert second["op"] == ">=" and second["value"].startswith("2026-10-06T12:05:00")


async def test_a_text_term_is_echoed_as_normalised_words(api: Api) -> None:
    body = (await api.get("/v1/runs", token=READER, q='"Deploy  API" dep*')).json()
    phrase, prefix = body["query"]["terms"]
    assert phrase["lexemes"] == ["deploy", "api"] and phrase["phrase"] is True
    assert prefix["lexemes"] == ["dep"] and prefix["prefix"] is True and prefix["kind"] == "text"


async def test_run_row_only_queries_see_every_run_and_report_no_unindexed_count(
    api: Api, ids: dict[str, str]
) -> None:
    response = await api.get("/v1/runs", token=READER, q="status:success")
    assert names(ids, response) == {"ok", "secure", "multi", "unindexed"}
    body = response.json()
    assert body["query"]["unindexed"] is None
    assert all(item["matched"] is None for item in body["items"])  # no index terms: nothing to say


async def test_an_index_term_does_not_see_unindexed_runs_and_says_how_many_there_are(
    api: Api, ids: dict[str, str]
) -> None:
    response = await api.get("/v1/runs", token=READER, q="-tool:nosuchtool")
    assert "unindexed" not in names(ids, response) and "queued" not in names(ids, response)
    assert response.json()["query"]["unindexed"] == 2


async def test_an_empty_query_executes_exactly_the_statements_of_the_plain_listing(
    api: Api, ids: dict[str, str]
) -> None:
    with statements(api) as plain:
        base = await api.get("/v1/runs", token=READER)
    with statements(api) as empty:
        searched = await api.get("/v1/runs", token=READER, q="   ")
    assert plain == empty
    assert item_ids(base) == item_ids(searched)
    body = searched.json()
    assert body["query"]["terms"] == [] and body["facets"] is None
    assert all(item["matched"] is None for item in body["items"])


async def test_a_search_runs_at_most_two_statements_against_the_index(
    api: Api, ids: dict[str, str]
) -> None:
    with statements(api) as seen:
        response = await api.get("/v1/runs", token=READER, q="tool:github file:*.ts hello")
    assert response.status_code == 200
    assert 1 <= len(mentioning(seen, "run_search")) <= 2


async def test_without_q_the_response_carries_no_search_parts(
    api: Api, ids: dict[str, str]
) -> None:
    body = (await api.get("/v1/runs", token=READER)).json()
    assert body["query"] is None and body["facets"] is None
    assert all(item["matched"] is None for item in body["items"])


async def test_q_is_anded_with_the_legacy_filters(api: Api, ids: dict[str, str]) -> None:
    got = await api.get("/v1/runs", token=READER, q="tool:github", status="FAILED")
    assert names(ids, got) == {"fail"}
    got = await api.get("/v1/runs", token=READER, q="tool:github", agent_id="planner")
    assert names(ids, got) == {"multi"}
    got = await api.get(
        "/v1/runs", token=READER, q="tool:github", started_after="2026-10-04T00:00:00Z"
    )
    assert names(ids, got) == {"fail", "running"}


async def test_a_cursor_pages_a_search_without_gaps_or_repeats(
    api: Api, ids: dict[str, str]
) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = (
            await api.get("/v1/runs", token=READER, q="-tool:nosuchtool", limit=2, cursor=cursor)
        ).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 5  # the five indexed runs


async def test_a_bad_query_is_a_422_with_a_position_that_depends_only_on_the_text(
    api: Api, ids: dict[str, str]
) -> None:
    response = await api.get("/v1/runs", token=READER, q="tool:github cost:>abc")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "QUERY_INVALID" and error["details"]["position"] == 18
    assert error["details"]["reason"]
    assert "abc" not in response.text.replace("cost:>abc", "")  # the reason never quotes data


async def test_a_content_field_is_allowed_for_a_key_with_payload_read(
    api: Api, ids: dict[str, str]
) -> None:
    assert names(ids, await api.get("/v1/runs", token=READER, q="command:curl")) == {"fail"}
    assert names(ids, await api.get("/v1/runs", token=READER, q="s3cr3t")) == {"fail"}


async def test_a_key_without_read_scope_is_refused_before_the_query_is_parsed(api: Api) -> None:
    response = await api.get("/v1/runs", token="ingest_only", q="cost:>abc")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_a_project_bound_key_searches_only_its_own_project(
    api: Api, ids: dict[str, str]
) -> None:
    assert names(ids, await api.get("/v1/runs", token="reader", q="tool:github")) == {
        "fail",
        "running",
        "multi",
    }
    mine = await api.get("/v1/runs", token="beta", q="tool:github")  # another project's key
    assert item_ids(mine) == []
    assert mine.json()["query"]["unindexed"] == 0
    assert all(not v for v in mine.json()["facets"].values())
