"""Hostile query text (AC-6): every input is a 422 QUERY_INVALID or a literal match, never a 500."""

import logging

import pytest

from abb_api.core.logging import JsonFormatter
from tests.api_fixtures import Api
from tests.search.http_helpers import Spec, ingest_spec, item_ids

READER = "wide_reader"

HOSTILE = [
    "x" * 513,
    "x" * 10_000,
    " ".join(f"tool:t{i}" for i in range(17)),
    " ".join(f"w{i}" for i in range(9)),
    "name:" + "y" * 257,
    "a\x00b",
    "a\x1fb",
    'unbalanced "quote',
    "tool:'",
    "cost:>1e400",
    "cost:>NaN",
    "retries:>=9223372036854775808",
    "started:9999-99-99",
    "started:>=-99999999d",
    "started:>=0001-01-01T00:00:00+14:00",
    "duration:>1y",
    "event:../../etc",
    "status:drop",
    "file:" + "*a" * 9,
    "name:%",
    "name:_",
    "name:\\",
    "'",
    ";--",
    "!!!",
    "@@",
    "a & b | !c (d) e:* f <-> g",
    "'; DROP TABLE runs; --",
    "‮evil‬ ́́́",
    "\U0001f600 \U0001f4a5*",
    "finding:x verdict:y",
    "-",
    '-""',
    '"',
    "\\",
    "tool:",
    ":x",
    "*",
    "**",
    "-*",
]


@pytest.mark.parametrize("q", HOSTILE)
async def test_hostile_input_is_a_422_or_a_literal_never_a_500(
    api: Api, q: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        response = await api.get("/v1/runs", token=READER, q=q)
    assert response.status_code in (200, 422), (q, response.status_code, response.text)
    if response.status_code == 422:
        error = response.json()["error"]
        assert error["code"] == "QUERY_INVALID" and error["details"]["reason"]
        assert 0 <= error["details"]["position"] <= 10_000
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], q


async def test_an_unpaired_surrogate_is_not_a_server_error(api: Api) -> None:
    headers = api.headers(READER)
    del headers["content-type"]
    response = await api.client.get("/v1/runs?q=%ED%A0%80", headers=headers)
    assert response.status_code in (200, 422), response.text


async def test_like_metacharacters_in_a_name_match_only_themselves(api: Api) -> None:
    spec: Spec = [("run.started", {"run.name": "50% done_ok"}, {})]
    plain: Spec = [("run.started", {"run.name": "500 doneXok"}, {})]
    hit = (await ingest_spec(api, spec))["run_id"]
    miss = (await ingest_spec(api, plain))["run_id"]
    await api.drain()
    assert item_ids(await api.get("/v1/runs", token=READER, q="name:%")) == [hit]
    assert item_ids(await api.get("/v1/runs", token=READER, q="name:_ok")) == [hit]
    assert item_ids(await api.get("/v1/runs", token=READER, q="name:0%")) == [hit]
    assert miss not in item_ids(await api.get("/v1/runs", token=READER, q="name:%d"))


async def test_tsquery_syntax_in_free_text_is_a_separator_never_an_operator(api: Api) -> None:
    run = await ingest_spec(api, [("run.started", {"run.name": "alpha beta"}, {})])
    await api.drain()
    found = [run["run_id"]]
    for q in ("alpha&beta", "alpha beta", "(alpha) beta", "alpha<->beta"):
        assert item_ids(await api.get("/v1/runs", token=READER, q=q)) == found, q
    # An OR (or a negation) would still match the run; separators make both words required.
    for q in ("alpha|nosuchword", "!alpha nosuchword"):
        assert item_ids(await api.get("/v1/runs", token=READER, q=q)) == [], q


async def test_the_query_text_never_reaches_the_logs(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await api.get("/v1/runs", token=READER, q="LOGCANARYword tool:LOGCANARYtool")
        await api.get("/v1/runs", token=READER, q="cost:>LOGCANARYbad")
    formatter = JsonFormatter("test")
    rendered = "\n".join(formatter.format(r) for r in caplog.records)
    assert "logcanary" not in rendered.lower()
