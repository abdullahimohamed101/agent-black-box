"""Parser for the §152 filter grammar: a hand-written scanner, linear in the query length.

    query := term*                       (whitespace separated, flat conjunction)
    term  := '-'? ( field ':' op? value | value )
    value := '"' chars '"' | bare        (a quoted value escapes \\" and \\\\)

Everything is bounded before it is interpreted: 512 characters, 16 terms, 8 text terms, 256
characters per value, 16 words per text term, 8 wildcards per glob. Errors carry a character
position and a fixed reason; neither ever depends on stored data (ADR-101). User text is only
ever turned into typed values here; the compiler binds them as parameters.
"""

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from abb_api.clock import Clock, system_clock
from abb_api.db.tables import RUN_STATUSES
from abb_api.search.ast import FieldTerm, Query, Term, TextTerm, Value
from abb_api.search.errors import QueryError
from abb_api.search.fields import CATALOGUE, OPERATORS, RESERVED, Field, Kind
from abb_api.search.tokens import normalise, tokenize

MAX_QUERY_CHARS = 512
MAX_TERMS = 16
MAX_TEXT_TERMS = 8
MAX_VALUE_CHARS = 256
MAX_WORDS = 16
MAX_WILDCARDS = 8
MAX_RELATIVE_DAYS = 3660
_MIN_YEAR, _MAX_YEAR = 1970, 2999

_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*:")
# The event schema's `AgentSlug` and the `agent_id` rule of `GET /v1/runs`, so a legacy
# `?agent=my_agent` bookmark converts to a valid term (review F2).
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DECIMAL = re.compile(r"-?[0-9]{1,20}(\.[0-9]{1,9})?")
_INT = re.compile(r"-?[0-9]{1,10}")
_DURATION = re.compile(r"([0-9]{1,12}(?:\.[0-9]{1,6})?)(ms|s|m|h)")
_RELATIVE = re.compile(r"-([0-9]{1,7})([mhdw])")
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?(Z|z|[+-][0-9]{2}:[0-9]{2})"
)
_EVENT_PREFIX = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]*){0,3}")
_EVENT = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}")
_DURATION_MS = {"ms": 1.0, "s": 1_000.0, "m": 60_000.0, "h": 3_600_000.0}
_RELATIVE_SECONDS = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}


def _is_space(char: str) -> bool:
    return char in " \t\n\r" or unicodedata.category(char) in ("Zs", "Zl", "Zp")


def _check_characters(q: str) -> None:
    for index, char in enumerate(q):
        if _is_space(char):
            continue
        if unicodedata.category(char) in ("Cc", "Cs"):
            raise QueryError(index, "control character")


class _Scanner:
    def __init__(self, q: str, clock: Clock) -> None:
        self.q = q
        self.n = len(q)
        self.clock = clock
        self.terms: list[Term] = []
        self.text_terms = 0

    # ------------------------------------------------------------------ scanning

    def run(self) -> Query:
        i = 0
        while i < self.n:
            if _is_space(self.q[i]):
                i += 1
                continue
            if len(self.terms) >= MAX_TERMS:
                raise QueryError(i, "too many terms")
            i = self._term(i)
        return Query(tuple(self.terms))

    def _term(self, start: int) -> int:
        i, negated = start, False
        if self.q[i] == "-" and i + 1 < self.n and not _is_space(self.q[i + 1]):
            negated, i = True, i + 1
        match = _FIELD_NAME.match(self.q, i)
        if match:
            return self._field_term(start, i, match.end() - 1, negated)
        return self._text_term(start, i, negated)

    def _read_quoted(self, i: int) -> tuple[str, int]:
        """The unescaped text of a quoted value starting at the quote `i`, and the next index."""
        chars: list[str] = []
        j = i + 1
        while j < self.n:
            char = self.q[j]
            if char == "\\" and j + 1 < self.n and self.q[j + 1] in '"\\':
                chars.append(self.q[j + 1])
                j += 2
                continue
            if char == '"':
                if j + 1 < self.n and not _is_space(self.q[j + 1]):
                    raise QueryError(j + 1, "unexpected character after the closing quote")
                return "".join(chars), j + 1
            chars.append(char)
            j += 1
        raise QueryError(i, "unterminated quote")

    def _read_value(self, i: int) -> tuple[str, bool, int]:
        if self.q[i] == '"':
            value, end = self._read_quoted(i)
            return value, True, end
        j = i
        while j < self.n and not _is_space(self.q[j]):
            j += 1
        return self.q[i:j], False, j

    # ------------------------------------------------------------------ text terms

    def _text_term(self, start: int, i: int, negated: bool) -> int:
        if self.text_terms >= MAX_TEXT_TERMS:
            raise QueryError(start, "too many text terms")
        raw, _, end = self._read_value(i)
        if len(raw) > MAX_VALUE_CHARS:
            raise QueryError(start, "value too long")
        quoted = self.q[i] == '"'
        prefix = raw.endswith("*")
        lexemes = tokenize(raw[:-1] if prefix else raw)
        if not lexemes:
            raise QueryError(start, "no searchable characters")
        if len(lexemes) > MAX_WORDS:
            raise QueryError(start, "too many words in a term")
        self.text_terms += 1
        self.terms.append(TextTerm(tuple(lexemes), quoted, prefix, negated, (start, end)))
        return end

    # ------------------------------------------------------------------ field terms

    def _field_term(self, start: int, name_at: int, colon: int, negated: bool) -> int:
        name = self.q[name_at:colon].lower()
        if name in RESERVED or name not in CATALOGUE:
            raise QueryError(name_at, "unknown field")
        field = CATALOGUE[name]
        i = colon + 1
        op = "="
        if field.ordered:
            for candidate in OPERATORS:
                if self.q.startswith(candidate, i):
                    op, i = candidate, i + len(candidate)
                    break
        if i >= self.n or _is_space(self.q[i]):
            raise QueryError(i, "missing value")
        value_at = i
        raw, quoted, end = self._read_value(i)
        if len(raw) > MAX_VALUE_CHARS:
            raise QueryError(value_at, "value too long")
        prefix = False
        if field.prefix and raw.endswith("*"):
            raw, prefix = raw[:-1], True
        if not raw:
            raise QueryError(value_at, "missing value")
        resolved = _interpret(field, op, raw, prefix, value_at + (1 if quoted else 0), self.clock)
        self.terms.append(
            FieldTerm(resolved[0], resolved[1], resolved[2], negated, (start, end), prefix)
        )
        return end


def _fail(at: int, reason: str) -> QueryError:
    return QueryError(at, reason)


def _interpret(
    field: Field, op: str, raw: str, prefix: bool, at: int, clock: Clock
) -> tuple[str, str, Value]:
    """(field name, operator, typed value); an alias may rename the field and operator."""
    kind = field.kind
    name = field.name
    if kind is Kind.ENUM:
        status = raw.upper()
        if status not in RUN_STATUSES:
            raise _fail(at, "unknown status")
        return name, op, status
    if kind is Kind.SLUG:
        slug = normalise(raw)
        if not _SLUG.fullmatch(slug):
            raise _fail(at, "not a valid agent slug")
        return name, op, slug
    if kind in (Kind.TEXT, Kind.GLOB):
        if kind is Kind.GLOB:
            wildcards = len(re.findall(r"\*+|\?", raw))
            if wildcards > MAX_WILDCARDS:
                raise _fail(at, "too many wildcards")
        return name, op, raw
    if kind is Kind.DECIMAL:
        if not _DECIMAL.fullmatch(raw):
            raise _fail(at, "not a number")
        return name, op, Decimal(raw)
    if kind is Kind.INT:
        if not _INT.fullmatch(raw) or abs(int(raw)) > 2_147_483_647:
            raise _fail(at, "not an integer in range")
        return name, op, int(raw)
    if kind is Kind.DURATION:
        match = _DURATION.fullmatch(raw.lower())
        if not match:
            raise _fail(at, "not a duration (use ms, s, m or h)")
        return name, op, float(match.group(1)) * _DURATION_MS[match.group(2)]
    if kind is Kind.TIME:
        resolved_op, instant = _time(op, raw, at, clock)
        return name, resolved_op, instant
    if kind is Kind.NAME:
        return name, op, normalise(raw)
    if kind is Kind.EVENT:
        event = raw.lower()
        pattern = _EVENT_PREFIX if prefix else _EVENT
        if len(event) > 64 or not pattern.fullmatch(event):
            raise _fail(at, "not an event type")
        return name, op, event
    if kind is Kind.BOOL:
        word = raw.lower()
        if word not in ("true", "false"):
            raise _fail(at, "expected true or false")
        return name, op, word == "true"
    if kind is Kind.SEVERITY:
        if raw.lower() != "error":
            raise _fail(at, "only severity:error is supported")
        return "errors", ">", 0
    raise AssertionError(kind)  # pragma: no cover


def _time(op: str, raw: str, at: int, clock: Clock) -> tuple[str, Value]:
    relative = _RELATIVE.fullmatch(raw)
    if relative:
        if op == "=":
            raise _fail(at, "a relative time needs a comparison operator")
        seconds = int(relative.group(1)) * _RELATIVE_SECONDS[relative.group(2)]
        if seconds > MAX_RELATIVE_DAYS * 86_400:
            raise _fail(at, "relative time too far back")
        return op, clock().astimezone(UTC) - timedelta(seconds=seconds)
    date_match = _DATE.fullmatch(raw)
    if date_match:
        year, month, day = (int(g) for g in date_match.groups())
        if not _MIN_YEAR <= year <= _MAX_YEAR:
            raise _fail(at, "date out of range")
        try:
            midnight = datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            raise _fail(at, "not a valid date") from None
        if op == "=":
            return "between", (midnight, midnight + timedelta(days=1))
        return op, midnight
    if _INSTANT.fullmatch(raw):
        try:
            parsed = datetime.fromisoformat(raw.upper())
            instant = parsed.astimezone(UTC)
        except (ValueError, OverflowError):
            raise _fail(at, "not a valid time") from None
        if not _MIN_YEAR <= instant.year <= _MAX_YEAR:
            raise _fail(at, "time out of range")
        return op, instant
    raise _fail(at, "not a date, time or relative time (like -24h)")


def parse(q: str, clock: Clock = system_clock) -> Query:
    """The typed query for `q`, or `QueryError`. Resolves relative times against `clock()`."""
    if len(q) > MAX_QUERY_CHARS:
        raise QueryError(MAX_QUERY_CHARS, "query too long")
    _check_characters(q)
    return _Scanner(q, clock).run()
