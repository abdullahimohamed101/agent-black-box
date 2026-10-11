"""The shared tokeniser (plan D4): one lexeme stream for documents and queries."""

import pytest

from abb_api.search.tokens import expansions, normalise, positions, tokenize


def test_words_paths_and_dotted_names_are_single_lexemes() -> None:
    assert tokenize("npm test -- src/auth.test.ts") == ["npm", "test", "src/auth.test.ts"]
    assert tokenize("foo_bar claude-3.5-sonnet") == ["foo_bar", "claude-3.5-sonnet"]


def test_leading_and_trailing_punctuation_is_trimmed() -> None:
    assert tokenize("-force --force. /etc/passwd/ ...") == ["force", "force", "etc/passwd"]


def test_text_is_nfkc_normalised_and_casefolded() -> None:
    assert tokenize("\uff43\uff55\uff52\uff4c CURL Straße") == ["curl", "curl", "strasse"]


def test_normalise_is_idempotent() -> None:
    for text in ["ǅ", "ß", "ﬃ", "İ", "Ⅷ", "\uff46\uff55\uff4c\uff4c"]:
        assert normalise(normalise(text)) == normalise(text)


def test_marks_stay_inside_words_and_emoji_separate() -> None:
    assert tokenize("éclair 😀x") == [normalise("éclair"), "x"]
    assert tokenize("😀😀") == []


def test_overlong_tokens_are_dropped() -> None:
    assert tokenize("a" * 257 + " ok") == ["ok"]
    assert tokenize("a" * 256) == ["a" * 256]


def test_expansion_covers_segments_and_parts_in_order() -> None:
    assert expansions("src/auth.ts") == ["src/auth.ts", "src", "auth.ts", "auth", "ts"]
    assert expansions("plain") == ["plain"]


def test_segments_share_the_position_of_their_token() -> None:
    lexemes, truncated = positions(["open src/auth.ts"], max_positions=100)
    assert not truncated
    assert lexemes["open"] == [1]
    assert lexemes["src/auth.ts"] == lexemes["src"] == lexemes["auth"] == lexemes["ts"] == [2]


def test_values_are_separated_by_a_gap_so_phrases_never_span_them() -> None:
    lexemes, _ = positions(["npm", "test"], max_positions=100)
    assert lexemes["test"][0] - lexemes["npm"][0] == 2


@pytest.mark.parametrize("limit", [1, 5, 8])
def test_positions_are_capped_and_the_cap_is_reported(limit: int) -> None:
    text = " ".join(f"w{i}" for i in range(50))
    lexemes, truncated = positions([text], max_positions=limit)
    assert truncated
    assert max(p for ps in lexemes.values() for p in ps) <= limit
