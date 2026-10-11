"""The typed query: what the parser produces and the compiler consumes (spec §152).

A flat conjunction of terms. Spans are 0-based `[start, end)` character offsets into the query
string, covering the whole term including a leading `-`; the UI uses them to render chips and to
remove one term exactly.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

Span = tuple[int, int]

# `=` is exact; `between` carries a `(start, end)` half-open range (a whole UTC day).
Operator = str
Value = str | int | float | Decimal | datetime | tuple[datetime, datetime]


@dataclass(frozen=True)
class FieldTerm:
    field: str
    op: Operator
    value: Value
    negated: bool
    span: Span
    prefix: bool = False


@dataclass(frozen=True)
class TextTerm:
    lexemes: tuple[str, ...]
    phrase: bool
    prefix: bool
    negated: bool
    span: Span


Term = FieldTerm | TextTerm


@dataclass(frozen=True)
class Query:
    terms: tuple[Term, ...] = ()

    def field_terms(self) -> tuple[FieldTerm, ...]:
        return tuple(t for t in self.terms if isinstance(t, FieldTerm))

    def text_terms(self) -> tuple[TextTerm, ...]:
        return tuple(t for t in self.terms if isinstance(t, TextTerm))
