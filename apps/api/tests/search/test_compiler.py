"""The compiler and its content gate, without a database (AC-2).

Statements are captured by a fake connection and rendered with the PostgreSQL dialect: what is
asserted is the SQL text an actor's query would send.
"""

import itertools
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.runs.queries import RunQueries
from abb_api.search.compiler import (
    Matcher,
    compile_query,
    escape_like,
    glob_pattern,
)
from abb_api.search.parser import parse
from abb_api.search.reads import SearchBudget
from abb_api.tenancy import TenantContext

WORKSPACE = uuid.uuid4()
NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)
CANARY = "CMDCANARY-s3cr3t"
CONTENT_WORDS = ("content_doc", "file_paths", "commands", "content_truncated")


def principal(*held: str) -> Principal:
    return Principal(
        kind="user",
        workspace_id=WORKSPACE,
        project_id=None,
        actions=frozenset({actions.RUN_READ, *held}),
        actor_id="user:test",
    )


VIEWER = principal()
OWNER = principal(actions.PAYLOAD_READ)

FIELD_TERMS = [
    "status:failed",
    "agent:planner",
    "agent:plan*",
    "root_agent:coder",
    "name:deploy",
    "cost:>1.5",
    "retries:>=2",
    "errors:>0",
    "severity:error",
    "duration:>60s",
    "started:>=-24h",
    "started:2026-10-01",
    "event:shell.*",
    "tool:github",
    "model:claude*",
    "provider:anthropic",
    "tag:prod",
    "security:true",
    "security:false",
]
CONTENT_TERMS = ["file:src/auth/**", "command:curl", 'file:"/etc/passwd"']
TEXT_TERMS = [CANARY, f"{CANARY}*", f'"{CANARY} x"', "src/auth.ts", "npm test", '"npm test"']


def corpus(*, content: bool) -> list[str]:
    """Fields x text x negation, plus mixes; content fields only when asked."""
    terms = FIELD_TERMS + TEXT_TERMS + (CONTENT_TERMS if content else [])
    queries = list(terms)
    queries += [f"-{t}" for t in terms]
    queries += [" ".join(pair) for pair in itertools.combinations(terms[:8] + TEXT_TERMS, 2)]
    queries += [" ".join(f"-{t}" if i % 2 else t for i, t in enumerate(terms[:16]))]
    return queries


class Capture:
    """Stands in for an AsyncConnection: records rendered statements, returns no rows."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> list[Any]:
        compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
        self.statements.append(str(compiled))
        self.params.append(dict(compiled.params))
        return []


async def statements_for(q: str, who: Principal) -> Capture:
    compiled = compile_query(parse(q, lambda: NOW), who)
    capture = Capture()
    queries = RunQueries(capture, TenantContext(WORKSPACE))  # type: ignore[arg-type]
    await queries.page(limit=51, search=compiled)
    await queries.facets_and_unindexed(compiled, budget=SearchBudget(5.0))
    return capture


def search_statements(capture: Capture) -> list[str]:
    return [s for s in capture.statements if "run_search" in s or "runs" in s]


# ------------------------------------------------------------------ the content gate


@pytest.mark.parametrize(
    "q", [*CONTENT_TERMS, "-file:x", "FILE:x", "-command:x", "tool:a COMMAND:x"]
)
def test_content_fields_are_reported_and_nothing_is_built_for_a_viewer(q: str) -> None:
    compiled = compile_query(parse(q, lambda: NOW), VIEWER)
    assert compiled.refused and compiled.predicates == () and not compiled.needs_index
    assert set(compiled.content_fields) <= {"file", "command"} and compiled.content_fields
    owner = compile_query(parse(q, lambda: NOW), OWNER)
    assert (
        not owner.refused and owner.predicates and owner.content_fields == compiled.content_fields
    )


def test_the_reported_field_is_the_canonical_name_in_first_use_order() -> None:
    compiled = compile_query(parse("COMMAND:x FILE:y Command:z", lambda: NOW), VIEWER)
    assert compiled.content_fields == ("command", "file")


async def test_a_refused_search_cannot_be_executed() -> None:
    compiled = compile_query(parse("file:x", lambda: NOW), VIEWER)
    queries = RunQueries(Capture(), TenantContext(WORKSPACE))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await queries.page(limit=10, search=compiled)
    with pytest.raises(ValueError):
        await queries.facets_and_unindexed(compiled, budget=SearchBudget(1.0))


async def test_no_statement_for_a_viewer_ever_mentions_a_content_column() -> None:
    for q in corpus(content=False):
        capture = await statements_for(q, VIEWER)
        for sql in capture.statements:
            for word in CONTENT_WORDS:
                assert word not in sql, (q, word)
        assert not any(CANARY in p for params in capture.params for p in map(str, params.values()))


async def test_the_same_corpus_reaches_the_content_document_for_text_with_payload_read() -> None:
    for q in [*TEXT_TERMS, f"-{CANARY}"]:
        capture = await statements_for(q, OWNER)
        page = capture.statements[0]
        assert "content_doc" in page and "metadata_doc" in page, q
    capture = await statements_for("tool:github", OWNER)
    assert "content_doc" not in capture.statements[0]  # only text terms read the document
    assert "content_truncated" in capture.statements[0]  # the owner may know it
    capture = await statements_for("file:x command:y", OWNER)
    assert "file_paths" in capture.statements[0] and "commands" in capture.statements[0]


async def test_viewer_page_statement_selects_only_metadata_flags_and_arrays() -> None:
    capture = await statements_for("tool:github", VIEWER)
    page = capture.statements[0]
    assert "metadata_truncated" in page and "content_truncated" not in page


# ------------------------------------------------------------------ the statements' shape


async def test_every_statement_binds_the_workspace() -> None:
    for who in (VIEWER, OWNER):
        for q in corpus(content=who is OWNER)[::7]:
            capture = await statements_for(q, who)
            for sql in (s for s in capture.statements if "FROM runs" in s):
                assert "runs.workspace_id =" in sql, (q, sql)
            joined = [s for s in capture.statements if "JOIN run_search" in s]
            # the tenant is stated again on the joined side, as a bound parameter
            assert all("run_search.workspace_id = %(" in s for s in joined)


async def test_user_strings_are_bound_parameters_only() -> None:
    queries = [
        f"name:{CANARY}",
        f"tool:{CANARY.lower()}",
        f'"{CANARY}"',
        f"{CANARY}*",
        'name:"\'; drop table runs;--"',
    ]
    for q in queries:
        capture = await statements_for(q, VIEWER)
        for sql in capture.statements:
            assert CANARY.lower() not in sql.lower() and "drop table" not in sql.lower()
        assert any(
            CANARY.lower() in str(value).lower() or "drop table" in str(value)
            for params in capture.params
            for value in params.values()
        )


async def test_tsquery_is_cast_from_a_literal_and_to_tsquery_is_never_used() -> None:
    for q in TEXT_TERMS:
        for who in (VIEWER, OWNER):
            capture = await statements_for(q, who)
            sql = " ".join(capture.statements).lower()
            assert "cast(" in sql and "as tsquery" in sql
            assert "to_tsquery" not in sql and "plainto_tsquery" not in sql
            assert "to_tsvector" not in sql and "websearch_to_tsquery" not in sql


async def test_tsquery_operators_in_text_are_lexemes_in_the_literal() -> None:
    capture = await statements_for('"a&b|c!d <-> (e)"', VIEWER)
    literals = [v for v in capture.params[0].values() if isinstance(v, str) and "'" in v]
    assert literals and all("|" not in v and "!" not in v for v in literals)


async def test_negation_of_nullable_columns_requires_a_value() -> None:
    capture = await statements_for("-name:x -duration:>60s -root_agent:a", VIEWER)
    page = capture.statements[0]
    assert page.count("IS NOT NULL") == 3


async def test_search_without_index_terms_does_not_join_and_facets_left_join() -> None:
    capture = await statements_for("status:failed cost:>1", VIEWER)
    page, facets = capture.statements[0], capture.statements[-1]
    assert "run_search" not in page
    assert "LEFT OUTER JOIN run_search" in facets
    assert "NOT (EXISTS" not in facets  # no unindexed count for a query that does not need it
    indexed = await statements_for("tool:github", VIEWER)
    assert "JOIN run_search" in indexed.statements[0]
    assert "ORDER BY run_search.started_at DESC, run_search.run_id DESC" in indexed.statements[0]
    assert "NOT (EXISTS" in indexed.statements[-1]  # the unindexed count rides along


async def test_a_search_is_at_most_two_run_search_statements() -> None:
    capture = await statements_for("tool:github -model:x text", VIEWER)
    assert len([s for s in capture.statements if "run_search" in s]) == 2


async def test_facets_cover_metadata_arrays_only() -> None:
    capture = await statements_for("tool:github", VIEWER)
    facets = capture.statements[-1]
    for name in ("agents", "tools", "models", "event_types"):
        assert f"u_{name}" in facets
    for forbidden in ("tags", "providers", "file_paths", "commands", "content"):
        assert forbidden not in facets
    assert "LIMIT" in facets and "row_number()" in facets


# ------------------------------------------------------------------ patterns and matching


def test_like_escaping_order() -> None:
    assert escape_like(r"a\b%c_d") == r"a\\b\%c\_d"


@pytest.mark.parametrize(
    ("glob", "pattern"),
    [
        ("src/auth/**", "%src/auth/%"),
        ("*.ts", "%%.ts"),
        ("/etc/passwd", "/etc/passwd"),
        ("/home/*/.ssh", "/home/%/.ssh"),
        ("passwd", "%passwd%"),
        ("a_b%", "%a\\_b\\%%"),
        ("file?.txt", "%file_.txt"),
        ("/x?", "/x_"),
    ],
)
def test_glob_patterns(glob: str, pattern: str) -> None:
    assert glob_pattern(glob) == pattern


def test_matched_is_the_intersection_with_the_runs_arrays() -> None:
    compiled = compile_query(
        parse("tool:github model:claude* -tool:slack event:shell.* tag:nope text", lambda: NOW),
        VIEWER,
    )
    assert set(compiled.matchers) == {
        Matcher("tools", "github", False),
        Matcher("models", "claude", True),
        Matcher("event_types", "shell.", True),
        Matcher("tags", "nope", False),
    }
    arrays = {
        "tools": ["github", "slack"],
        "models": ["claude-3.5", "claude-4", "gpt"],
        "event_types": ["shell.command.completed", "run.started"],
        "tags": ["prod"],
        "agents": ["x"],
    }
    assert compiled.matched(arrays) == {
        "tools": ["github"],
        "models": ["claude-3.5", "claude-4"],
        "event_types": ["shell.command.completed"],
    }


def test_providers_and_negated_terms_are_not_reported_as_matched() -> None:
    compiled = compile_query(parse("provider:a -tool:b status:failed", lambda: NOW), VIEWER)
    assert compiled.matchers == ()


def test_row_only_queries_do_not_need_the_index() -> None:
    assert not compile_query(
        parse("status:failed cost:>1 name:x started:>=-1d", lambda: NOW), VIEWER
    ).needs_index
    assert compile_query(parse("status:failed errors:>0", lambda: NOW), VIEWER).needs_index
    assert compile_query(parse("hello", lambda: NOW), VIEWER).needs_index
