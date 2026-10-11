"""Serialisers for tsvector and tsquery *literals*, bound as `CAST(:x AS tsvector|tsquery)`.

The `tsvector` and `tsquery` input functions do no parsing or normalisation of the lexemes: what
is quoted is the lexeme. That is the only way to get exactly our tokens into Postgres (see
`tokens.py`); `to_tsvector`/`to_tsquery` are never used. Bounds follow Postgres' own: a position
is 1..16383, a lexeme under 2047 bytes.
"""

from collections.abc import Mapping, Sequence

MAX_POSITION = 16_383
MAX_LEXEME_BYTES = 2_046


def _quote(lexeme: str) -> str:
    if not lexeme:
        raise ValueError("empty lexeme")
    if len(lexeme.encode()) > MAX_LEXEME_BYTES:
        raise ValueError("lexeme too long")
    if "\x00" in lexeme:
        raise ValueError("NUL in lexeme")
    return "'" + lexeme.replace("\\", "\\\\").replace("'", "''") + "'"


def tsvector_literal(positions: Mapping[str, Sequence[int]]) -> str:
    """`'a':1,4 'b':2`. Lexemes are sorted for a canonical (hashable) form."""
    parts: list[str] = []
    for lexeme in sorted(positions):
        where = sorted(set(positions[lexeme]))
        if not where or where[0] < 1 or where[-1] > MAX_POSITION:
            raise ValueError("position out of range")
        parts.append(f"{_quote(lexeme)}:{','.join(str(p) for p in where)}")
    return " ".join(parts)


def tsquery_literal(lexemes: Sequence[str], *, phrase: bool, prefix: bool) -> str:
    """All lexemes ANDed, or adjacent (`<->`) when `phrase`; `prefix` marks the last one only."""
    if not lexemes:
        raise ValueError("a query needs at least one lexeme")
    operands = [
        _quote(lexeme) + (":*" if prefix and index == len(lexemes) - 1 else "")
        for index, lexeme in enumerate(lexemes)
    ]
    return (" <-> " if phrase else " & ").join(operands)


def parse_tsvector(literal: str) -> dict[str, list[int]]:
    """Inverse of `tsvector_literal` (for round-trip tests; accepts only what we emit)."""
    result: dict[str, list[int]] = {}
    index, length = 0, len(literal)
    while index < length:
        if literal[index] == " ":
            index += 1
            continue
        if literal[index] != "'":
            raise ValueError("expected a quote")
        index += 1
        chars: list[str] = []
        while True:
            if index >= length:
                raise ValueError("unterminated lexeme")
            char = literal[index]
            if char == "\\":
                chars.append(literal[index + 1])
                index += 2
            elif char == "'":
                if index + 1 < length and literal[index + 1] == "'":
                    chars.append("'")
                    index += 2
                else:
                    index += 1
                    break
            else:
                chars.append(char)
                index += 1
        if index >= length or literal[index] != ":":
            raise ValueError("expected positions")
        index += 1
        end = literal.find(" ", index)
        end = length if end == -1 else end
        result["".join(chars)] = [int(p) for p in literal[index:end].split(",")]
        index = end
    return result
