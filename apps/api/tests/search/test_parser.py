"""The §152 grammar (AC-1): examples, negation, limits, spans and a hostile fuzzer."""

import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from abb_api.search.ast import FieldTerm, Query, TextTerm
from abb_api.search.errors import QueryError
from abb_api.search.parser import parse

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def terms(q: str) -> tuple[Any, ...]:
    return parse(q, clock).terms


def one(q: str) -> Any:
    (term,) = terms(q)
    return term


def field(q: str) -> FieldTerm:
    term = one(q)
    assert isinstance(term, FieldTerm)
    return term


def text(q: str) -> TextTerm:
    term = one(q)
    assert isinstance(term, TextTerm)
    return term


def fail(q: str) -> QueryError:
    with pytest.raises(QueryError) as caught:
        parse(q, clock)
    return caught.value


# ------------------------------------------------------------------ the spec examples


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("status:failed", ("status", "=", "FAILED")),
        ("STATUS:Timed_Out", ("status", "=", "TIMED_OUT")),
        ("cost:>1.00", ("cost", ">", Decimal("1.00"))),
        ("retries:>=3", ("retries", ">=", 3)),
        ('model:"provider/model"', ("model", "=", "provider/model")),
        ("tool:github", ("tool", "=", "github")),
        ('file:"src/auth/**"', ("file", "=", "src/auth/**")),
        ("event:shell.command.completed", ("event", "=", "shell.command.completed")),
        ("severity:error", ("errors", ">", 0)),
        ("security:true", ("security", "=", True)),
        ("security:FALSE", ("security", "=", False)),
        ("started:>=2026-10-01", ("started", ">=", datetime(2026, 10, 1, tzinfo=UTC))),
        ("started:>=-24h", ("started", ">=", NOW - timedelta(hours=24))),
        ("started:<-2w", ("started", "<", NOW - timedelta(weeks=2))),
        ("duration:>60s", ("duration", ">", 60_000.0)),
        ("duration:<=1.5h", ("duration", "<=", 5_400_000.0)),
        ("duration:>250ms", ("duration", ">", 250.0)),
        ("agent:planner", ("agent", "=", "planner")),
        ("root_agent:Planner", ("root_agent", "=", "planner")),
        ("name:deploy", ("name", "=", "deploy")),
        ("command:curl", ("command", "=", "curl")),
        ("errors:>0", ("errors", ">", 0)),
        ("tag:Prod", ("tag", "=", "prod")),
        ("provider:anthropic", ("provider", "=", "anthropic")),
    ],
)
def test_every_example_parses_to_the_expected_term(q: str, expected: tuple[Any, ...]) -> None:
    term = field(q)
    assert (term.field, term.op, term.value) == expected
    assert not term.negated and term.span == (0, len(q))


def test_prefixes() -> None:
    glob = field('model:"claude*"')
    assert (glob.value, glob.prefix) == ("claude", True)
    assert field("event:shell.*").value == "shell." and field("event:shell.*").prefix
    assert field("agent:plan*").prefix
    # `*` is a literal where prefixes are not defined
    assert field("name:a*b").value == "a*b" and not field("name:a*b").prefix
    assert text("src/au*").prefix and text("src/au*").lexemes == ("src/au",)
    assert text('"npm te*"').lexemes == ("npm", "te") and text('"npm te*"').prefix


def test_a_date_for_equals_is_the_whole_utc_day() -> None:
    term = field("started:2026-10-01")
    assert term.op == "between"
    assert term.value == (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC))
    assert field("started:<2026-10-01").value == datetime(2026, 10, 1, tzinfo=UTC)


def test_rfc3339_instants_normalise_to_utc() -> None:
    assert field("started:>=2026-10-01T10:30:00Z").value == datetime(
        2026, 10, 1, 10, 30, tzinfo=UTC
    )
    assert field("started:>=2026-10-01T10:30:00+02:00").value == datetime(
        2026, 10, 1, 8, 30, tzinfo=UTC
    )
    assert field("started:>2026-10-01T10:30:00.25z").value == datetime(
        2026, 10, 1, 10, 30, 0, 250_000, tzinfo=UTC
    )


# ------------------------------------------------------------------ free text and negation


def test_free_text_words_and_phrases() -> None:
    assert text("curl").lexemes == ("curl",) and not text("curl").phrase
    assert text("CURL,Bearer").lexemes == ("curl", "bearer")
    phrase = text('"npm test"')
    assert phrase.lexemes == ("npm", "test") and phrase.phrase and not phrase.prefix


def test_several_terms_and_spans() -> None:
    q = 'status:failed tool:github  retries:>=2 started:>=-7d "npm test"'
    parsed = parse(q, clock)
    assert [t.span for t in parsed.terms] == [(0, 13), (14, 25), (27, 38), (39, 52), (53, 63)]
    assert [q[a:b] for a, b in (t.span for t in parsed.terms)][-1] == '"npm test"'


def test_negation() -> None:
    assert field("-status:failed").negated
    assert field("-status:failed").span == (0, 14)
    assert text("-curl").negated and text("-curl").lexemes == ("curl",)
    assert text('-"npm test"').negated


def test_double_dash_negates_once_and_the_second_dash_is_the_value() -> None:
    term = text("--force")
    assert term.negated and term.lexemes == ("force",)
    quoted = text('-"--force"')
    assert quoted.negated and quoted.phrase and quoted.lexemes == ("force",)
    assert not text('"--force"').negated


def test_a_dash_after_an_operator_belongs_to_the_value() -> None:
    zero = field("cost:>-0")
    assert not zero.negated and zero.value == Decimal("-0")
    assert not field("started:>=-24h").negated
    assert field("-started:>=-24h").negated


def test_lone_dash_and_dashed_words() -> None:
    assert fail("-").reason == "no searchable characters"
    assert text("foo-bar").lexemes == ("foo-bar",) and not text("foo-bar").negated


def test_quoted_values_escape_quote_and_backslash() -> None:
    assert field(r'name:"say \"hi\" \\ done"').value == 'say "hi" \\ done'
    assert text(r'"a \"b\""').lexemes == ("a", "b")
    assert field(r"name:\\").value == "\\\\"


def test_whitespace_only_and_empty_queries_have_no_terms() -> None:
    assert parse("", clock) == Query()
    assert parse(" \t\n ", clock) == Query()


def test_field_names_are_case_insensitive_and_unknown_names_are_refused() -> None:
    assert field("Tool:x").field == "tool"
    for q in ("nope:x", "finding:x", "verdict:passed", "http://x"):
        error = fail(q)
        assert error.reason == "unknown field" and error.position == 0
    assert fail("-nope:x").position == 1
    assert text('"http://x"').lexemes == ("http", "x")


# ------------------------------------------------------------------ limits and errors


def test_query_length_limit() -> None:
    assert len(parse("name:" + "a" * 256 + " name:" + "b" * 245, clock).terms) == 2  # 512 chars
    assert fail("a" * 513).reason == "query too long"
    assert fail("a" * 10_000).reason == "query too long"


def test_term_limits() -> None:
    sixteen = " ".join("status:failed" for _ in range(16))
    assert len(parse(sixteen, clock).terms) == 16
    error = fail(sixteen + " status:failed")
    assert (error.reason, error.position) == ("too many terms", len(sixteen) + 1)
    eight = " ".join(f"w{i}" for i in range(8))
    assert len(parse(eight, clock).terms) == 8
    error = fail(eight + " w9")
    assert (error.reason, error.position) == ("too many text terms", len(eight) + 1)


def test_value_limits() -> None:
    assert field("name:" + "a" * 256).value == "a" * 256
    assert (fail("name:" + "a" * 257).reason, fail("name:" + "a" * 257).position) == (
        "value too long",
        5,
    )
    assert fail("a" * 257).reason == "value too long"
    assert fail('"' + "a" * 257 + '"').reason == "value too long"


def test_text_term_word_limit() -> None:
    assert len(text('"' + " ".join("w" * 3 for _ in range(16)) + '"').lexemes) == 16
    assert (
        fail('"' + " ".join("w" * 3 for _ in range(17)) + '"').reason == "too many words in a term"
    )


def test_glob_wildcard_limit() -> None:
    assert field("file:" + "a*" * 8).value == "a*" * 8
    assert fail("file:" + "a*" * 9).reason == "too many wildcards"
    assert field("file:a**b").value == "a**b"  # a run of stars counts once
    assert fail("file:" + "?" * 9).reason == "too many wildcards"


def test_relative_time_limit() -> None:
    assert field("started:>=-3660d").value == NOW - timedelta(days=3660)
    assert fail("started:>=-3661d").reason == "relative time too far back"
    assert fail("started:>=-99999999d").reason == "not a date, time or relative time (like -24h)"
    assert fail("started:-24h").reason == "a relative time needs a comparison operator"


@pytest.mark.parametrize(
    ("q", "position", "reason"),
    [
        ("a\x00b", 1, "control character"),
        ("a\x1fb", 1, "control character"),
        ("ok \x7f", 3, "control character"),
        ("\ud800", 0, "control character"),
        ('"abc', 0, "unterminated quote"),
        ('name:"abc', 5, "unterminated quote"),
        ('"abc"d', 5, "unexpected character after the closing quote"),
        ("status:", 7, "missing value"),
        ("status: failed", 7, "missing value"),
        ("cost:>", 6, "missing value"),
        ("event:*", 6, "missing value"),
        ("status:drop", 7, "unknown status"),
        ("cost:>1e400", 6, "not a number"),
        ("cost:>NaN", 6, "not a number"),
        ("cost:>1.0000000001", 6, "not a number"),
        ("retries:>=9223372036854775808", 10, "not an integer in range"),
        ("retries:1.5", 8, "not an integer in range"),
        ("duration:>1y", 10, "not a duration (use ms, s, m or h)"),
        ("duration:>60", 10, "not a duration (use ms, s, m or h)"),
        ("started:9999-99-99", 8, "date out of range"),
        ("started:2026-02-30", 8, "not a valid date"),
        ("started:0001-01-01", 8, "date out of range"),
        ("started:>=0001-01-01T00:00:00+14:00", 10, "not a valid time"),
        ("started:>=2026-10-01T10:30", 10, "not a date, time or relative time (like -24h)"),
        ("event:../../etc", 6, "not an event type"),
        ("event:shell", 6, "not an event type"),
        ("agent:-bad", 6, "not a valid agent slug"),
        ("security:maybe", 9, "expected true or false"),
        ("severity:warning", 9, "only severity:error is supported"),
        ("started:today", 8, "not a date, time or relative time (like -24h)"),
        ("!!!", 0, "no searchable characters"),
        ("a @@", 2, "no searchable characters"),
        ("-@@", 0, "no searchable characters"),
        ("😀", 0, "no searchable characters"),
        ("*", 0, "no searchable characters"),
    ],
)
def test_errors_carry_the_position_and_a_fixed_reason(q: str, position: int, reason: str) -> None:
    error = fail(q)
    assert (error.position, error.reason) == (position, reason)


def test_quoted_value_positions_point_inside_the_quotes() -> None:
    assert fail('status:"drop"').position == 8


def test_search_operators_in_text_are_just_words() -> None:
    assert text("a&b").lexemes == ("a", "b")
    assert text("a<->b").lexemes == ("a", "b")
    assert text("'(a)|!b'").lexemes == ("a", "b")
    assert text("a\\b").lexemes == ("a", "b")


def test_operators_on_unordered_fields_are_part_of_the_value() -> None:
    assert field("name:>=x").value == ">=x"
    assert fail("status:>=failed").reason == "unknown status"


def test_bare_not_and_or_are_words() -> None:
    assert [t.lexemes for t in terms("NOT OR")] == [("not",), ("or",)]


# ------------------------------------------------------------------ the fuzzer

_ALPHABET = [
    *"abcXYZ019 \t\n:-\"\\*?<>=!&|()'%_./,;@#$^~`+{}[]",
    "status:",
    "cost:>",
    "-",
    "--",
    "file:",
    "started:>=",
    '"',
    '\\"',
    "\uff43",
    "😀",
    "\u0301",
    "\u202e",
    "\x00",
    "\x1f",
    "\ud800",
    "1e400",
    "-24h",
    "2026-10-01",
    "T",
    "Z",
    "*",
    "event:",
]


def test_fuzzer_never_raises_anything_but_query_error_and_stays_fast() -> None:
    rng = random.Random(20261010)
    slowest = 0.0
    for _ in range(10_000):
        length = rng.choice([0, 1, 2, 5, 20, 80, 300, 600])
        q = "".join(rng.choice(_ALPHABET) for _ in range(length))
        started = time.perf_counter()
        try:
            result = parse(q, clock)
        except QueryError as error:
            assert 0 <= error.position <= max(len(q), 512)
            assert error.reason
        else:
            assert len(result.terms) <= 16
            for term in result.terms:
                assert 0 <= term.span[0] < term.span[1] <= len(q)
        slowest = max(slowest, time.perf_counter() - started)
    assert slowest < 0.010, f"slowest parse took {slowest * 1000:.1f} ms"
