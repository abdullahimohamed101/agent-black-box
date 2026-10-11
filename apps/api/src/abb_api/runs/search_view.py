"""The search parts of a run listing's response: the parsed-query echo, `matched` and facets.

Everything here is derived from the parsed query, the catalogue and metadata arrays only; there is
no content in any of it (ADR-101). The echo depends on the query text and the caller's permissions,
never on stored data.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from abb_api.runs.queries import FacetCounts, SearchRowData
from abb_api.runs.schemas import (
    FacetsOut,
    FacetValueOut,
    MatchedOut,
    QueryFieldOut,
    QueryOut,
    QueryTermOut,
)
from abb_api.search.ast import FieldTerm, Query, Term
from abb_api.search.compiler import CompiledSearch
from abb_api.search.fields import CATALOGUE


def _text(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _term_out(term: Term) -> QueryTermOut:
    if isinstance(term, FieldTerm):
        value, end = term.value, None
        if isinstance(value, tuple):
            value, end = value
        return QueryTermOut(
            kind="field",
            field=term.field,
            op=term.op,
            value=_text(value),
            value_end=_text(end) if end is not None else None,
            lexemes=None,
            phrase=False,
            prefix=term.prefix,
            negated=term.negated,
            span=term.span,
        )
    return QueryTermOut(
        kind="text",
        field=None,
        op=None,
        value=None,
        value_end=None,
        lexemes=list(term.lexemes),
        phrase=term.phrase,
        prefix=term.prefix,
        negated=term.negated,
        span=term.span,
    )


def query_out(query: Query, *, content_allowed: bool, counts: FacetCounts | None) -> QueryOut:
    fields = [
        QueryFieldOut(
            name=f.name,
            kind=f.kind.value,
            description=f.description,
            ordered=f.ordered,
            prefix=f.prefix,
        )
        for f in CATALOGUE.values()
        if content_allowed or not f.needs_content
    ]
    return QueryOut(
        terms=[_term_out(t) for t in query.terms],
        fields=fields,
        content_allowed=content_allowed,
        unindexed=counts.unindexed if counts else None,
        unindexed_capped=counts.unindexed_capped if counts else False,
    )


def facets_out(counts: FacetCounts) -> FacetsOut:
    def values(pairs: Sequence[tuple[str, int]]) -> list[FacetValueOut]:
        return [FacetValueOut(value=v, count=n) for v, n in pairs]

    f = counts.facets
    return FacetsOut(
        status=values(f["status"]),
        agents=values(f["agents"]),
        tools=values(f["tools"]),
        models=values(f["models"]),
        event_types=values(f["event_types"]),
    )


def matched_out(compiled: CompiledSearch, row: SearchRowData | None) -> MatchedOut | None:
    if row is None:
        return None
    found = compiled.matched(row.arrays)
    return MatchedOut(
        tools=found.get("tools", []),
        models=found.get("models", []),
        event_types=found.get("event_types", []),
        agents=found.get("agents", []),
        tags=found.get("tags", []),
        metadata_truncated=row.metadata_truncated,
        content_truncated=row.content_truncated,
    )
