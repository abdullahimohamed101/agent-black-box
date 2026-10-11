"""tsvector and tsquery literal serialisers: no database (AC-2, review F1)."""

import random

import pytest

from abb_api.search.tokens import positions, tokenize
from abb_api.search.tsliteral import (
    MAX_LEXEME_BYTES,
    MAX_POSITION,
    parse_tsvector,
    tsquery_literal,
    tsvector_literal,
)


def test_tsvector_quotes_backslashes_and_spaces() -> None:
    literal = tsvector_literal({"it's": [1], "a\\b": [2], "x y": [3, 1], "a:b": [4]})
    assert literal == r"""'a:b':4 'a\\b':2 'it''s':1 'x y':1,3"""
    assert parse_tsvector(literal) == {"it's": [1], "a\\b": [2], "x y": [1, 3], "a:b": [4]}


@pytest.mark.parametrize(
    "bad",
    [
        {"": [1]},
        {"a": []},
        {"a": [0]},
        {"a": [MAX_POSITION + 1]},
        {"a" * (MAX_LEXEME_BYTES + 1): [1]},
    ],
)
def test_tsvector_bounds(bad: dict[str, list[int]]) -> None:
    with pytest.raises(ValueError):
        tsvector_literal(bad)


def test_tsvector_boundary_values_are_accepted() -> None:
    assert tsvector_literal({"a" * MAX_LEXEME_BYTES: [MAX_POSITION]})


def test_tsquery_shapes() -> None:
    assert tsquery_literal(["npm"], phrase=False, prefix=False) == "'npm'"
    assert tsquery_literal(["a", "b"], phrase=False, prefix=False) == "'a' & 'b'"
    assert tsquery_literal(["npm", "test"], phrase=True, prefix=False) == "'npm' <-> 'test'"
    # the prefix marks the last lexeme only (review F28)
    assert tsquery_literal(["src/au", "x"], phrase=False, prefix=True) == "'src/au' & 'x':*"
    assert tsquery_literal(["it's"], phrase=False, prefix=False) == "'it''s'"


def test_tsquery_operators_in_text_are_only_ever_lexemes() -> None:
    lexemes = tokenize("a & b | !c <-> (d) e:*")
    assert all(not set("&|!<>():*'\\ ") & set(lexeme) for lexeme in lexemes)
    literal = tsquery_literal(lexemes, phrase=False, prefix=False)
    assert literal.count("&") == len(lexemes) - 1 and "|" not in literal and "!" not in literal


def test_tsquery_rejects_empty_and_overlong() -> None:
    with pytest.raises(ValueError):
        tsquery_literal([], phrase=False, prefix=False)
    with pytest.raises(ValueError):
        tsquery_literal(["a" * (MAX_LEXEME_BYTES + 1)], phrase=False, prefix=False)


def test_round_trip_of_expanded_documents() -> None:
    lexemes, _ = positions(["curl -H 'Authorization: Bearer x' src/auth.ts it's"], max_positions=50)
    assert parse_tsvector(tsvector_literal(lexemes)) == {k: sorted(v) for k, v in lexemes.items()}


def test_same_position_expansion_in_the_literal() -> None:
    lexemes, _ = positions(["x src/auth.ts"], max_positions=50)
    parsed = parse_tsvector(tsvector_literal(lexemes))
    assert parsed["src/auth.ts"] == parsed["src"] == parsed["auth.ts"] == parsed["auth"] == [2]
    assert parsed["ts"] == [2]


def test_every_document_token_matches_itself_its_prefix_and_its_neighbour() -> None:
    """The property behind AC-3's database checks, stated on the literals alone."""
    rng = random.Random(7)
    words = ["src/auth.ts", "npm", "test", "foo_bar", "claude-3.5", "a.b.c", "\uff58", "naïve"]
    for _ in range(200):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(2, 8)))
        tokens = tokenize(text)
        document, _ = positions([text], max_positions=100)
        for index, token in enumerate(tokens):
            # itself: the token is a lexeme of the document
            assert token in document
            # as a prefix of itself: some lexeme starts with every prefix of the token
            for cut in (1, len(token)):
                assert any(lex.startswith(token[:cut]) for lex in document)
            # as a phrase with its neighbour: the two tokens sit at adjacent positions
            if index + 1 < len(tokens):
                nxt = tokens[index + 1]
                assert any(q + 1 in document[nxt] for q in document[token]), (text, token, nxt)
                assert tsquery_literal([token, nxt], phrase=True, prefix=False).count("<->") == 1
