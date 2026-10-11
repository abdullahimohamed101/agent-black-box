"""`GET /v1/runs?q=` end to end (AC-3 HTTP half, AC-7 statement counts): the public contract."""

import pytest

from tests.api_fixtures import Api
from tests.search.http_helpers import (
    Spec,
    ingest_spec,
    item_ids,
    mentioning,
    seed_named,
    statements,
)
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


async def test_a_search_page_has_no_total_count_and_no_relevance_field(
    api: Api, ids: dict[str, str]
) -> None:
    """ADR-101 (4): no count of matches and no ranking, in any shape of response (they are oracles)."""
    for q in ("tool:github", "status:failed", "github", "-tool:nothing"):
        body = (await api.get("/v1/runs", token=READER, q=q)).json()
        assert set(body) <= {"items", "next_cursor", "query", "facets", "facets_skipped"}
        assert set(body["query"]) == {
            "terms",
            "fields",
            "content_allowed",
            "unindexed",
            "unindexed_capped",
        }
        for item in body["items"]:
            assert not {"rank", "score", "relevance", "total", "count"} & set(item)
    # newest first, whatever the terms (no ranking): the order is the plain listing's
    plain = [i["started_at"] for i in (await api.get("/v1/runs", token=READER)).json()["items"]]
    assert plain == sorted(plain, reverse=True)


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


async def test_a_120_run_search_pages_without_gaps_or_repeats_while_new_runs_arrive(
    api: Api,
) -> None:
    """Keyset paging: every run present throughout is returned exactly once, newer arrivals never
    repeat, and an arrival behind the cursor may show up but never displaces anything."""
    spec: Spec = [("run.started", {}, {}), ("tool.call.completed", {"tool.name": "keyset"}, {})]
    original = {(await ingest_spec(api, spec, days_ago=0, minutes=i))["run_id"] for i in range(120)}
    await api.drain()
    assert len(original) == 120

    seen: list[str] = []
    arrivals: list[str] = []
    cursor = None
    for page_number in range(20):
        page = (
            await api.get("/v1/runs", token=READER, q="tool:keyset", limit=25, cursor=cursor)
        ).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if page_number == 1:  # a newer run (before the cursor) and an older one (behind it)
            for minutes in (500, -500):
                arrivals.append(
                    (await ingest_spec(api, spec, days_ago=0, minutes=minutes))["run_id"]
                )
            await api.drain()
        if cursor is None:
            break
    assert len(seen) == len(set(seen))  # no repeats
    assert original <= set(seen)  # no gaps
    newer, older = arrivals
    assert newer not in seen  # it sorts before the cursor already passed
    assert set(seen) - original <= {older}  # at most the late older run joins the tail
