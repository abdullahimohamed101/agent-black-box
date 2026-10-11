"""Search is never an oracle for withheld content (AC-4, ADR-101).

For an actor without `payload.read` nothing about a search may depend on captured commands, paths
or other content: not which runs match, not their order, counts, cursors, facets, `matched` or
flags, not the error bodies and not the statements run. Every check below builds a pair of queries
that differ only in hidden content and requires the two responses to be identical, except for the
echo of the query text the caller typed himself (`query.terms`).

The world seeds acme's first run with a command, a working directory, a file path and an event
under an unregistered attribute, each carrying a canary; the other two acme runs carry none.
"""

import json
import logging
import uuid

import httpx
import pytest
from abb_event_schema.ids import IdKind, from_uuid

from abb_api.audit.repository import AuditRepository
from tests.authz.test_content_withheld import SEES_CONTENT, WITHHELD_FROM
from tests.authz.world import (
    COMMAND_CANARY,
    CONTENT_CANARIES,
    CUSTOM_CANARY,
    PATH_CANARY,
    World,
)
from tests.search.http_helpers import mentioning, statements

NONSENSE = "NOSUCHTERMx9"
FULLWIDTH_CURL = "".join(chr(ord(c) + 0xFEE0) for c in "curl")  # NFKC folds it to "curl"
ALLOWED = "tool:acme-agent-tool"  # metadata present on all three acme runs
CONTENT_WORDS = [
    COMMAND_CANARY,
    "CMDCANARY*",
    "s3cr3t",
    "PATHCANARY",
    "id_rsa",
    PATH_CANARY,
    "CWDCANARY",
    "CUSTOMCANARY*",
    CUSTOM_CANARY,
    FULLWIDTH_CURL,
    "curl",
    "src/au*",
]
# Words that really are in the owner's index (the rest prove the viewer cannot tell either way).
FOUND_BY_OWNER = [
    COMMAND_CANARY,
    "CMDCANARY*",
    "PATHCANARY",
    "id_rsa",
    PATH_CANARY,
    "CWDCANARY",
    "CUSTOMCANARY*",
    CUSTOM_CANARY,
]
FORMS = [
    "{w}",
    '"{p}"',
    "-{w}",
    '-"{p}"',
    f"{ALLOWED} {{w}}",
    f"{{w}} {ALLOWED}",
    "-tool:nosuchtool {w}",
    f"{{w}} -{ALLOWED}",
]
REFUSED_PAIRS = [
    ("file:/home/PATHCANARY/.ssh/id_rsa", "file:/nothing/at/all"),
    ("file:PATHCANARY*", "file:zzznotthere*"),
    ("command:CMDCANARY-curl-H-Bearer-s3cr3t", "command:zzznotthere"),
    ("-file:/home/PATHCANARY/.ssh/id_rsa", "-file:/nothing/at/all"),
    ("-command:curl", "-command:zzznotthere"),
    ("FILE:x", "file:x"),
    ("Command:x", "command:y"),
    (f"{ALLOWED} command:CMDCANARY*", f"{ALLOWED} command:zzz*"),
    ("status:success -file:PATHCANARY", "status:success -file:zzz"),
    (f"curl {ALLOWED} file:PATHCANARY", f"nosuch {ALLOWED} file:nothing"),
]


def form(template: str, word: str) -> str:
    return template.format(w=word, p=word.rstrip("*"))


def nonsense_for(word: str) -> str:
    return NONSENSE + ("*" if word.endswith("*") else "")


def without_echo(response: httpx.Response) -> str:
    """The response as canonical JSON minus what varies by design: request ids and the echo of the
    caller's own query text."""
    body = response.json()
    if "error" in body:
        body["error"].pop("request_id", None)
    elif body.get("query"):
        body["query"].pop("terms", None)
    return json.dumps(body, sort_keys=True)


async def search(world: World, actor: str, q: str, **params: str | int) -> httpx.Response:
    return await world.get("/v1/runs", actor, q=q, **params)


# ------------------------------------------------------------------ content fields are refused


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_a_content_field_is_refused_the_same_whether_or_not_anything_would_match(
    world: World,
    actor: str,
) -> None:
    for present, absent in REFUSED_PAIRS:
        bodies = []
        for q in (present, absent):
            with statements(world.api) as seen:
                response = await search(world, actor, q)
            assert response.status_code == 403, q
            error = response.json()["error"]
            assert error["code"] == "PERMISSION_DENIED", q
            assert error["details"]["required_permission"] == "payload.read"
            assert error["details"]["field"] in ("file", "command")  # canonical, never as typed
            for table in ("runs", "run_search", "events", "spans"):
                assert not mentioning(seen, table), (q, table)  # refused before any statement
            bodies.append(without_echo(response))
        assert bodies[0] == bodies[1], (present, absent)
    assert (await search(world, actor, "FILE:x")).json()["error"]["details"]["field"] == "file"
    both = await search(world, actor, "command:a file:b")  # first use in the query, not alphabetic
    assert both.json()["error"]["details"]["field"] == "command"


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_the_refusal_is_logged_and_audited_without_the_query(
    world: World,
    actor: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="abb_api.authz.audit")
    secret_query = "command:LOGGEDQUERYMARKER-s3cr3t"
    assert (await search(world, actor, secret_query)).status_code == 403
    lines = [r for r in caplog.records if r.getMessage() == "permission denied"]
    assert lines and getattr(lines[-1], "required_permission", None) == "payload.read"
    assert "LOGGEDQUERYMARKER" not in caplog.text
    async with world.api.engine.connect() as conn:
        rows = await AuditRepository(conn, world.api.tenant.context).recent(200)
    denied = [r for r in rows if r.outcome == "denied" and r.action == "payload.read"]
    assert denied and "LOGGEDQUERYMARKER" not in json.dumps([r.details for r in denied])
    assert denied[0].details["route"] == "/v1/runs"


async def test_billing_is_refused_before_the_query_is_read(world: World) -> None:
    bodies = []
    for q in ("status:failed", "cost:>abc", "file:x", NONSENSE, ""):
        response = await search(world, "billing", q)
        assert response.status_code == 403, q  # never a 422 that would show the grammar's verdict
        bodies.append(without_echo(response))
    assert len(set(bodies)) == 1


# ------------------------------------------------------------------ free text is not a probe


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_every_form_of_a_content_word_returns_what_a_nonsense_word_returns(
    world: World,
    actor: str,
) -> None:
    for word in CONTENT_WORDS:
        for template in FORMS:
            hidden = await search(world, actor, form(template, word), limit=1)
            blank = await search(world, actor, form(template, nonsense_for(word)), limit=1)
            where = (actor, form(template, word))
            assert hidden.status_code == blank.status_code == 200, where
            assert without_echo(hidden) == without_echo(blank), where
            # The same holds one page later: cursors carry positions, never matches.
            cursor = hidden.json()["next_cursor"]
            assert cursor == blank.json()["next_cursor"], where
            if cursor:
                later = await search(world, actor, form(template, word), limit=1, cursor=cursor)
                blank_later = await search(
                    world, actor, form(template, nonsense_for(word)), limit=1, cursor=cursor
                )
                assert without_echo(later) == without_echo(blank_later), where
        positive = (await search(world, actor, word)).json()
        assert positive["items"] == [] and positive["next_cursor"] is None, (actor, word)


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_no_response_to_a_viewer_search_carries_content_or_content_flags(
    world: World,
    actor: str,
) -> None:
    queries = [ALLOWED, "-" + NONSENSE, "status:success", "agent:acme-agent", "acme", "event:run.*"]
    queries += [form(t, w) for w in CONTENT_WORDS for t in FORMS[:2]]
    for q in queries:
        response = await search(world, actor, q)
        assert response.status_code == 200, q
        text = without_echo(response)
        for canary in CONTENT_CANARIES:
            assert canary.lower() not in text.lower(), (q, canary)
        assert '"content_truncated"' not in text, q  # the key does not exist for this actor
        body = response.json()
        if body["facets"] is not None:
            assert set(body["facets"]) == {"status", "agents", "tools", "models", "event_types"}
        assert {f["name"] for f in body["query"]["fields"]}.isdisjoint({"file", "command"})
        assert body["query"]["content_allowed"] is False
        for item in body["items"]:
            assert item["matched"] is None or "content_truncated" not in item["matched"]


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_the_statements_run_for_a_viewer_never_mention_content_columns(
    world: World,
    actor: str,
) -> None:
    for word in ("curl", "CMDCANARY*", "src/au*", NONSENSE):
        for template in FORMS:
            with statements(world.api) as seen:
                assert (await search(world, actor, form(template, word))).status_code == 200
            indexed = mentioning(seen, "run_search")
            assert 1 <= len(indexed) <= 2, (word, template)
            for statement in seen:
                for forbidden in ("content_doc", "file_paths", "commands", "content_truncated"):
                    assert forbidden not in statement, (word, template, forbidden)


async def test_the_same_check_would_catch_a_content_statement_for_an_owner(
    world: World,
) -> None:
    """Guards the statement assertion above: for an owner the content columns do appear."""
    with statements(world.api) as seen:
        assert (await search(world, "owner", "curl")).status_code == 200
    assert any("content_doc" in s for s in seen)
    with statements(world.api) as seen:
        assert (await search(world, "owner", "command:curl")).status_code == 200
    assert any("commands" in s for s in seen)


# ------------------------------------------------------------------ people who may read content


@pytest.mark.parametrize("actor", ("owner", "admin", "developer", "security", "wide_reader"))
async def test_actors_with_payload_read_find_the_content(world: World, actor: str) -> None:
    assert actor in SEES_CONTENT or actor == "wide_reader"
    first = world.acme.run_id
    for word in FOUND_BY_OWNER:
        found = await search(world, actor, word)
        assert [i["id"] for i in found.json()["items"]] == [first], (actor, word)
        item = found.json()["items"][0]
        assert item["matched"]["content_truncated"] is False
        rest = (await search(world, actor, f"-{word} {ALLOWED}")).json()["items"]
        assert first not in [i["id"] for i in rest] and len(rest) == 2, (actor, word)
    for q in ("command:CMDCANARY", "command:curl", "file:PATHCANARY", "file:*id_rsa"):
        assert [i["id"] for i in (await search(world, actor, q)).json()["items"]] == [first], q
    assert (await search(world, actor, "file:/nothing/at/all")).json()["items"] == []
    assert (await search(world, actor, "file:x")).status_code == 200
    assert (await search(world, actor, "command:x")).json()["query"]["content_allowed"] is True


# ------------------------------------------------------------------ keys and projects


async def test_a_key_that_cannot_read_runs_is_refused_before_parsing(world: World) -> None:
    response = await search(world, "ingest_only", "cost:>abc")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_another_projects_runs_are_reached_by_no_query(world: World) -> None:
    """A key bound to the empty `beta` project, and the alpha key asking for beta, see no alpha
    run, count none in `unindexed` or facets, and get the same bodies as for a project that does
    not exist."""
    beta = world.api.tenant.projects["beta"]
    for q in (ALLOWED, "curl", "command:curl", "-nosuch", "status:success"):
        mine = await search(world, "beta", q)
        assert mine.status_code == 200 and mine.json()["items"] == [], q
        body = mine.json()
        assert body["query"]["unindexed"] in (0, None), q
        assert all(not values for values in (body["facets"] or {}).values()), q
        for token in ("reader", "writer"):  # alpha-bound keys naming the other project
            foreign = await search(world, token, q, project_id=beta)
            absent = await search(
                world, token, q, project_id=from_uuid(IdKind.PROJECT, uuid.uuid4())
            )
            assert foreign.status_code == absent.status_code == 404, (token, q)
            assert without_echo(foreign) == without_echo(absent), (token, q)
