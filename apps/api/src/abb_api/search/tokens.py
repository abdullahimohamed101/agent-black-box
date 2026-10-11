"""The one tokeniser, shared by the document builder and the query parser.

Postgres' own text-search parser is never used on our text (it splits `foo_bar`, classifies
`src/auth.ts` as a file token and re-normalises): we build tsvector and tsquery *literals* from
the lexemes this module produces (`tsliteral.py`), so a document and a query agree by construction.

Rules: NFKC, case-fold, split on anything that is not a letter, mark, digit, `_`, `.`, `-` or `/`,
trim `.`, `-` and `/` from both ends of each token, drop tokens longer than 256 bytes. On the
**document side only**, a token's `/` segments and their `.` parts are also emitted, at the *same*
position as the token (`src/auth.ts` at 7 gives `src/auth.ts`, `src`, `auth.ts`, `auth`, `ts`, all
at 7), so phrase adjacency and prefix matching work on the unexpanded stream. The query side never
expands: each query token is one lexeme.
"""

import unicodedata
from collections.abc import Iterable

MAX_TOKEN_BYTES = 256
_WORD_PUNCTUATION = "_.-/"
_TRIM = ".-/"


def normalise(text: str) -> str:
    """NFKC, case-folded, NFKC again (casefolding can denormalise); idempotent."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())


def _is_token_char(char: str) -> bool:
    return char in _WORD_PUNCTUATION or unicodedata.category(char)[0] in "LNM"


def tokenize(text: str) -> list[str]:
    """The lexemes of `text`, in order (no expansion)."""
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        token = "".join(current).strip(_TRIM)
        current.clear()
        if token and len(token.encode()) <= MAX_TOKEN_BYTES:
            tokens.append(token)

    for char in normalise(text):
        if _is_token_char(char):
            current.append(char)
        else:
            flush()
    flush()
    return tokens


def expansions(token: str) -> list[str]:
    """The token itself, then its `/` segments, then their `.` parts; distinct, in that order."""
    seen = {token: None}
    segments = [s for s in token.split("/") if s]
    for segment in segments:
        seen.setdefault(segment, None)
    for segment in segments:
        for part in segment.split("."):
            if part:
                seen.setdefault(part, None)
    return list(seen)


def positions(texts: Iterable[str], *, max_positions: int) -> tuple[dict[str, list[int]], bool]:
    """Lexeme -> positions for the document side, and whether anything was cut off.

    Positions start at 1. Consecutive texts are separated by one empty position, so a phrase never
    spans two values. At most `max_positions` positions are used (gaps included).
    """
    lexemes: dict[str, list[int]] = {}
    position = 0
    truncated = False
    for text in texts:
        tokens = tokenize(text)
        if not tokens:
            continue
        if position:
            position += 1  # the gap between two values
        for token in tokens:
            position += 1
            if position > max_positions:
                truncated = True
                break
            for lexeme in expansions(token):
                lexemes.setdefault(lexeme, []).append(position)
        if truncated:
            break
    return lexemes, truncated
