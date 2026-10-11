"""The natural-language contract (AC-13): output is a grammar string that parses, goes through the
same gate as typed queries, and the module carries no vendor."""

import uuid
from pathlib import Path

import pytest

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.search import natural
from abb_api.search.compiler import compile_query
from abb_api.search.fields import CATALOGUE
from abb_api.search.natural import QueryCompiler, RuleBasedCompiler
from abb_api.search.parser import parse


def who(*held: str) -> Principal:
    return Principal(
        kind="user",
        workspace_id=uuid.uuid4(),
        project_id=None,
        actions=frozenset({actions.RUN_READ, *held}),
        actor_id="user:test",
    )


PHRASES = {
    "show failed runs": "status:failed",
    "runs that cost more than $5": "cost:>5",
    "more than 2.50 dollars": "cost:>2.50",
    "retried more than 3 times": "retries:>3",
    "failed runs from last week": "status:failed started:>=-7d",
    "runs touching src/auth/**": "file:src/auth/**",
    "runs that ran curl": "command:curl",
    "using tool github with errors": "errors:>0 tool:github",
    "runs with security events today": "started:>=-24h security:true",
}


def test_the_fake_satisfies_the_protocol() -> None:
    compiler: QueryCompiler = RuleBasedCompiler()
    assert compiler.compile("failed", fields=CATALOGUE) == "status:failed"


@pytest.mark.parametrize(("text", "expected"), PHRASES.items())
def test_each_phrase_compiles_to_a_grammar_string_that_parses(text: str, expected: str) -> None:
    out = RuleBasedCompiler().compile(text, fields=CATALOGUE)
    assert sorted(out.split()) == sorted(expected.split())
    query = parse(out)
    assert query.terms
    assert not compile_query(query, who(actions.PAYLOAD_READ)).refused


def test_content_phrases_are_refused_for_an_actor_without_payload_read() -> None:
    for text in ("runs touching /etc/passwd", "runs that ran curl"):
        compiled = compile_query(parse(RuleBasedCompiler().compile(text, fields=CATALOGUE)), who())
        assert compiled.refused and compiled.predicates == ()


def test_nothing_recognised_is_an_empty_query_and_input_is_bounded() -> None:
    assert RuleBasedCompiler().compile("hello there", fields=CATALOGUE) == ""
    assert RuleBasedCompiler().compile("failed " * 1000, fields=CATALOGUE) == "status:failed"


def test_fields_outside_the_catalogue_are_never_emitted() -> None:
    narrow = {k: v for k, v in CATALOGUE.items() if k != "file"}
    assert RuleBasedCompiler().compile("touching src/x", fields=narrow) == ""


def test_hostile_values_cannot_inject_extra_terms() -> None:
    out = RuleBasedCompiler().compile('touching a" cost:>1 tool:x', fields=CATALOGUE)
    assert [t.field for t in parse(out).field_terms()] == ["file"]


def test_the_module_has_no_vendor_and_no_sql() -> None:
    source = Path(natural.__file__).read_text().lower()
    assert "anthropic" not in source and "openai" not in source and "select " not in source
