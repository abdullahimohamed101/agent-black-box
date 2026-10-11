"""AST -> SQLAlchemy predicates over `runs` and `run_search`: the only place that knows the SQL.

The content gate (ADR-101) is here, at compile time: for an actor without `payload.read`,
`file:` and `command:` terms are *reported* (`content_fields`) and **no predicate is built**; the
service refuses them through the one `authorize_audited` path before any statement runs. Free text
compiles to the metadata document only, and no statement for such an actor can reference a content
column. User strings are bound parameters only; tsvector/tsquery are literals built in Python
(`tsliteral.py`) and cast, never passed through `to_tsquery`.
"""

import operator
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Select,
    and_,
    cast,
    exists,
    literal,
    not_,
    or_,
    select,
)
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import TSQUERY

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import holds
from abb_api.db import tables as t
from abb_api.search.ast import FieldTerm, Query, TextTerm
from abb_api.search.fields import CATALOGUE
from abb_api.search.tsliteral import tsquery_literal

_RS = t.run_search.c
_RUNS = t.runs.c
_COMPARE: dict[str, Callable[[Any, Any], ColumnElement[bool]]] = {
    "=": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}
_LIKE_ESCAPE = "\\"

# field -> (array column on run_search) for the fields that match one value of a text[]
_ARRAY_FIELDS = {
    "agent": "agents",
    "event": "event_types",
    "tool": "tools",
    "model": "models",
    "provider": "providers",
    "tag": "tags",
}
# run_row columns by field; the bool says the column may be NULL (a NULL never matches)
_RUN_FIELDS = {
    "status": (_RUNS.status, False),
    "root_agent": (_RUNS.agent_slug, True),
    "name": (_RUNS.name, True),
    "cost": (_RUNS.cost_usd, False),
    "retries": (_RUNS.retry_count, False),
    "duration": (_RUNS.duration_ms, True),
    "started": (_RUNS.started_at, False),
}
_INDEX_COLUMNS = {"errors": _RS.error_count, "security": _RS.security_events}

# What `matched` may name, and what facets may count: metadata arrays only (ADR-101 d).
MATCHABLE_ARRAYS = ("tools", "models", "event_types", "agents", "tags")
FACET_ARRAYS = ("agents", "tools", "models", "event_types")
_PAGE_ARRAYS = MATCHABLE_ARRAYS


def escape_like(value: str) -> str:
    """Escape `\\`, `%` and `_` (in that order) for a LIKE pattern with ESCAPE '\\'."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def glob_pattern(glob: str) -> str | None:
    """The LIKE pattern for a `file:` glob, or None when it is an exact path (plan D6).

    `*` and `**` match any characters including `/`; `?` matches one. A glob is anchored at the end
    and anchored at the start only when it begins with `/`. Without wildcards a glob is a
    substring, except an absolute path, which must match exactly.
    """
    wildcards = re.search(r"[*?]", glob) is not None
    if not wildcards:
        escaped = escape_like(glob)
        return escaped if glob.startswith("/") else f"%{escaped}%"
    parts = re.split(r"(\*+|\?)", glob)
    body = "".join("%" if p.startswith("*") else "_" if p == "?" else escape_like(p) for p in parts)
    return body if glob.startswith("/") else f"%{body}"


@dataclass(frozen=True)
class Matcher:
    """A non-negated term that names values of one array: what `matched` reports."""

    column: str
    value: str
    prefix: bool

    def hits(self, values: Sequence[str]) -> list[str]:
        return [v for v in values if (v.startswith(self.value) if self.prefix else v == self.value)]


@dataclass(frozen=True)
class CompiledSearch:
    predicates: tuple[ColumnElement[bool], ...]
    needs_index: bool
    content_fields: tuple[str, ...]  # canonical names, first use order; non-empty => refused
    content_allowed: bool
    matchers: tuple[Matcher, ...]
    # The predicates that need only `runs` (metadata by construction): the unindexed count applies
    # them so it counts runs that would otherwise have matched.
    row_predicates: tuple[ColumnElement[bool], ...] = ()

    @property
    def refused(self) -> bool:
        """True when the query names a content field the actor may not search: nothing is built."""
        return bool(self.content_fields) and not self.content_allowed

    def page_columns(self) -> list[Any]:
        """The run_search columns a page statement selects with each run (metadata only unless the
        actor holds `payload.read`, which alone may see `content_truncated`)."""
        columns = [getattr(_RS, name) for name in _PAGE_ARRAYS] + [_RS.metadata_truncated]
        if self.content_allowed:
            columns.append(_RS.content_truncated)
        return columns

    def matched(self, arrays: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
        """The values of the run's arrays that the query's terms named (intersection in Python)."""
        found: dict[str, list[str]] = {}
        for matcher in self.matchers:
            for value in matcher.hits(arrays.get(matcher.column, ())):
                bucket = found.setdefault(matcher.column, [])
                if value not in bucket:
                    bucket.append(value)
        return found


def _array_contains(column: Any, value: str, prefix: bool) -> ColumnElement[bool]:
    if not prefix:
        contains: ColumnElement[bool] = column.contains([value])  # `@>`, GIN-served where indexed
        return contains
    item = sa_func.unnest(column).column_valued("v")
    return exists(
        select(literal(1)).where(item.like(escape_like(value) + "%", escape=_LIKE_ESCAPE))
    )


def _array_like(column: Any, pattern: str) -> ColumnElement[bool]:
    item = sa_func.unnest(column).column_valued("v")
    return exists(select(literal(1)).where(item.ilike(pattern, escape=_LIKE_ESCAPE)))


def _array_equals_ci(column: Any, value: str) -> ColumnElement[bool]:
    return _array_like(column, escape_like(value))


def _field_predicate(term: FieldTerm) -> tuple[ColumnElement[bool], bool]:
    """(predicate before negation, whether it needs the index table)."""
    name, op, value = term.field, term.op, term.value
    if name in _ARRAY_FIELDS:
        assert isinstance(value, str)
        return _array_contains(getattr(_RS, _ARRAY_FIELDS[name]), value, term.prefix), True
    if name in _INDEX_COLUMNS:
        column = _INDEX_COLUMNS[name]
        if name == "security":
            return (column > 0 if value else column == 0), True
        return _COMPARE[op](column, value), True
    if name == "file":
        assert isinstance(value, str)
        pattern = glob_pattern(value)
        assert pattern is not None
        return _array_like(_RS.file_paths, pattern), True
    if name == "command":
        assert isinstance(value, str)
        return _array_like(_RS.commands, f"%{escape_like(value)}%"), True
    column, _ = _RUN_FIELDS[name]
    if name == "name":
        assert isinstance(value, str)
        return column.ilike(f"%{escape_like(value)}%", escape=_LIKE_ESCAPE), False
    if name == "root_agent":
        assert isinstance(value, str)
        if term.prefix:
            return column.like(escape_like(value) + "%", escape=_LIKE_ESCAPE), False
        return column == value, False
    if op == "between":
        assert isinstance(value, tuple)
        return and_(column >= value[0], column < value[1]), False
    return _COMPARE[op](column, value), False


def _text_predicate(term: TextTerm, *, content: bool) -> ColumnElement[bool]:
    literal_query = tsquery_literal(term.lexemes, phrase=term.phrase, prefix=term.prefix)
    query = cast(literal_query, TSQUERY)
    metadata = _RS.metadata_doc.op("@@", return_type=Boolean)(query)
    if not content:
        return metadata
    return or_(metadata, _RS.content_doc.op("@@", return_type=Boolean)(query))


def compile_query(query: Query, principal: Principal) -> CompiledSearch:
    """Compile for `principal`. Content fields without `payload.read` produce no predicates."""
    content_allowed = holds(principal, actions.PAYLOAD_READ)
    content_fields = tuple(
        dict.fromkeys(
            term.field
            for term in query.terms
            if isinstance(term, FieldTerm) and CATALOGUE[term.field].needs_content
        )
    )
    if content_fields and not content_allowed:
        return CompiledSearch((), False, content_fields, content_allowed, ())
    predicates: list[ColumnElement[bool]] = []
    row_predicates: list[ColumnElement[bool]] = []
    matchers: list[Matcher] = []
    needs_index = False
    for term in query.terms:
        if isinstance(term, TextTerm):
            predicate: ColumnElement[bool] = _text_predicate(term, content=content_allowed)
            index, nullable = True, False
        else:
            predicate, index = _field_predicate(term)
            nullable = _RUN_FIELDS.get(term.field, (None, False))[1]
            column = _ARRAY_FIELDS.get(term.field)
            if column in MATCHABLE_ARRAYS and not term.negated:
                assert isinstance(term.value, str) and column is not None
                matchers.append(Matcher(column, term.value, term.prefix))
        needs_index = needs_index or index
        if term.negated:
            column_not_null = (
                [_RUN_FIELDS[term.field][0].is_not(None)]
                if isinstance(term, FieldTerm) and nullable
                else []
            )
            predicate = and_(*column_not_null, not_(predicate))
        predicates.append(predicate)
        if not index:
            row_predicates.append(predicate)
    return CompiledSearch(
        tuple(predicates),
        needs_index,
        content_fields,
        content_allowed,
        tuple(matchers),
        tuple(row_predicates),
    )


def index_join(statement: Select[Any], workspace_id: Any) -> Select[Any]:
    """Inner join `run_search` on the full key, with the tenant stated again on the joined side."""
    return statement.join(
        t.run_search,
        and_(
            _RS.workspace_id == _RUNS.workspace_id,
            _RS.run_id == _RUNS.id,
            _RS.workspace_id == workspace_id,
        ),
    )
