"""Read side of runs: lookups and keyset-paginated listings, always inside one tenant.

A project-bound API key may only see its own project; `project_scope` carries that restriction
into every statement, so a run of another project is simply not found.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, exists, func, literal, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.jobs.outbox import SUMMARIZE_RUN
from abb_api.search.compiler import (
    FACET_ARRAYS,
    MATCHABLE_ARRAYS,
    CompiledSearch,
    index_join,
)
from abb_api.search.reads import SearchBudget
from abb_api.tenancy import TenantContext

FACET_SOURCE_RUNS = 1_000  # facets count the newest this-many matching runs
FACET_VALUES = 10  # top values per facet
UNINDEXED_CAP = 1_000  # the unindexed count stops here (`1000+` in the UI)


@dataclass(frozen=True)
class SearchRowData:
    """The run_search columns selected with a run on a search page (never content, except the
    content truncation flag for an actor who holds `payload.read`)."""

    arrays: dict[str, list[str]]
    metadata_truncated: bool
    content_truncated: bool | None  # None: the actor may not know


@dataclass(frozen=True)
class _Filters:
    """The listing's own (pre-search) filters, shared by the page and facets statements."""

    statuses: Sequence[str] = ()
    agent_slug: str | None = None
    project_id: uuid.UUID | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None


@dataclass(frozen=True)
class FacetCounts:
    facets: dict[str, list[tuple[str, int]]]
    unindexed: int | None  # None when the query does not need the index
    unindexed_capped: bool


@dataclass(frozen=True)
class RunRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str | None
    status: str
    agent_slug: str | None
    trace_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    ordering_mode: str
    summary: dict[str, Any]
    summary_version: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    search: SearchRowData | None = None


@dataclass(frozen=True)
class FindingsInputs:
    """What the detector job reads from the run row (everything else comes from the events)."""

    project_id: uuid.UUID
    agent_slug: str | None
    started_at: datetime
    ordering_mode: str
    summary_version: int
    cost_usd: Decimal
    retry_cost_usd: Decimal


_COLUMNS = (
    t.runs.c.id,
    t.runs.c.project_id,
    t.runs.c.name,
    t.runs.c.status,
    t.runs.c.agent_slug,
    t.runs.c.trace_id,
    t.runs.c.started_at,
    t.runs.c.completed_at,
    t.runs.c.duration_ms,
    t.runs.c.ordering_mode,
    t.runs.c.summary,
    t.runs.c.summary_version,
    t.runs.c.metadata,
    t.runs.c.created_at,
    t.runs.c.updated_at,
)


def _record(row: Any, search: CompiledSearch | None = None) -> RunRecord:
    data = {column.name: getattr(row, column.name) for column in _COLUMNS}
    if search is not None and search.needs_index:
        data["search"] = SearchRowData(
            arrays={name: list(getattr(row, name)) for name in MATCHABLE_ARRAYS},
            metadata_truncated=bool(row.metadata_truncated),
            content_truncated=bool(row.content_truncated) if search.content_allowed else None,
        )
    return RunRecord(**data)


class RunQueries:
    def __init__(
        self, conn: AsyncConnection, tenant: TenantContext, project_scope: uuid.UUID | None = None
    ) -> None:
        self._conn = conn
        self._tenant = tenant
        self._scope = project_scope

    def _base(self) -> Select[Any]:
        statement = select(*_COLUMNS).where(t.runs.c.workspace_id == self._tenant.workspace_id)
        if self._scope is not None:
            statement = statement.where(t.runs.c.project_id == self._scope)
        return statement

    async def get(self, run_id: uuid.UUID) -> RunRecord | None:
        row = (await self._conn.execute(self._base().where(t.runs.c.id == run_id))).first()
        return _record(row) if row else None

    def _filtered(self, statement: Select[Any], f: _Filters) -> Select[Any]:
        if f.statuses:
            statement = statement.where(t.runs.c.status.in_(list(f.statuses)))
        if f.agent_slug is not None:
            statement = statement.where(t.runs.c.agent_slug == f.agent_slug)
        if f.project_id is not None:
            statement = statement.where(t.runs.c.project_id == f.project_id)
        if f.started_after is not None:
            statement = statement.where(t.runs.c.started_at >= f.started_after)
        if f.started_before is not None:
            statement = statement.where(t.runs.c.started_at < f.started_before)
        return statement

    async def findings_inputs(self, run_id: uuid.UUID) -> FindingsInputs | None:
        c = t.runs.c
        row = (
            await self._conn.execute(
                select(
                    c.project_id,
                    c.agent_slug,
                    c.started_at,
                    c.ordering_mode,
                    c.summary_version,
                    c.cost_usd,
                    c.retry_cost_usd,
                ).where(c.workspace_id == self._tenant.workspace_id, c.id == run_id)
            )
        ).first()
        return FindingsInputs(**row._mapping) if row else None

    async def page(
        self,
        *,
        statuses: Sequence[str] = (),
        agent_slug: str | None = None,
        project_id: uuid.UUID | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        ascending: bool = False,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None = None,
        search: CompiledSearch | None = None,
        budget: SearchBudget | None = None,
    ) -> list[RunRecord]:
        """Up to `limit` runs after the cursor position (the caller asks for one extra row).

        With `search`, its predicates are ANDed in; a query that needs the index inner-joins
        `run_search` (an unindexed run matches no index term, positive or negated) and orders by
        the joined table's key. At most one statement; `budget` bounds it.
        """
        if search is not None and search.refused:
            raise ValueError("a refused search must not be executed")  # the gate is the caller's
        filters = _Filters(statuses, agent_slug, project_id, started_after, started_before)
        statement = self._filtered(self._base(), filters)
        started, ident = t.runs.c.started_at, t.runs.c.id
        if search is not None:
            if search.needs_index:
                statement = index_join(
                    statement.add_columns(*search.page_columns()), self._tenant.workspace_id
                )
                started, ident = t.run_search.c.started_at, t.run_search.c.run_id
            for predicate in search.predicates:
                statement = statement.where(predicate)
        key = tuple_(started, ident)
        if after is not None:
            position = tuple_(
                literal(after[0], t.runs.c.started_at.type), literal(after[1], t.runs.c.id.type)
            )
            statement = statement.where(key > position if ascending else key < position)
        order = (started.asc(), ident.asc()) if ascending else (started.desc(), ident.desc())
        statement = statement.order_by(*order).limit(limit)
        if budget is not None:
            result = await budget.run(self._conn, statement)
        else:
            result = await self._conn.execute(statement)
        return [_record(r, search) for r in result]

    async def facets_and_unindexed(
        self,
        search: CompiledSearch,
        *,
        statuses: Sequence[str] = (),
        agent_slug: str | None = None,
        project_id: uuid.UUID | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        budget: SearchBudget,
    ) -> FacetCounts:
        """Facet counts and the unindexed count in ONE statement (plan D8).

        Facets count the newest `FACET_SOURCE_RUNS` matching runs over metadata only: the run
        status and the agents, tools, models and event types arrays; top `FACET_VALUES` per
        facet. A query without index terms left-joins, so status still counts every run.
        `unindexed` counts runs in the request's scope (and matching its run-row terms) that have
        no document, capped; it never looks at content.
        """
        if search.refused:
            raise ValueError("a refused search must not be executed")
        runs, rs = t.runs.c, t.run_search.c
        scope = _Filters(statuses, agent_slug, project_id, started_after, started_before)
        matches = self._filtered(
            select(runs.status, *(getattr(rs, name) for name in FACET_ARRAYS)).select_from(t.runs),
            scope,
        ).where(runs.workspace_id == self._tenant.workspace_id)
        if self._scope is not None:
            matches = matches.where(runs.project_id == self._scope)
        if search.needs_index:
            matches = index_join(matches, self._tenant.workspace_id)
            order = (rs.started_at.desc(), rs.run_id.desc())
        else:
            matches = matches.outerjoin(
                t.run_search,
                and_(
                    rs.workspace_id == runs.workspace_id,
                    rs.run_id == runs.id,
                    rs.workspace_id == self._tenant.workspace_id,
                ),
            )
            order = (runs.started_at.desc(), runs.id.desc())
        for predicate in search.predicates:
            matches = matches.where(predicate)
        m = matches.order_by(*order).limit(FACET_SOURCE_RUNS).cte("matches")

        parts = [
            select(literal("status").label("k"), m.c.status.label("v"), func.count().label("n"))
            .group_by(m.c.status)
            .select_from(m)
        ]
        for name in FACET_ARRAYS:
            values = select(func.unnest(m.c[name]).label("v")).select_from(m).subquery(f"u_{name}")
            parts.append(
                select(literal(name).label("k"), values.c.v, func.count().label("n")).group_by(
                    values.c.v
                )
            )
        counted = union_all(*parts).cte("counted")
        ranked = select(
            counted.c.k,
            counted.c.v,
            counted.c.n,
            func.row_number()
            .over(partition_by=counted.c.k, order_by=(counted.c.n.desc(), counted.c.v))
            .label("rn"),
        ).cte("ranked")
        final: Any = select(ranked.c.k, ranked.c.v, ranked.c.n).where(ranked.c.rn <= FACET_VALUES)

        if search.needs_index:
            stray = self._filtered(select(literal(1)).select_from(t.runs), scope).where(
                runs.workspace_id == self._tenant.workspace_id,
                ~exists().where(
                    rs.workspace_id == runs.workspace_id,
                    rs.run_id == runs.id,
                    rs.workspace_id == self._tenant.workspace_id,
                ),
            )
            if self._scope is not None:
                stray = stray.where(runs.project_id == self._scope)
            for predicate in search.row_predicates:
                stray = stray.where(predicate)
            capped = stray.limit(UNINDEXED_CAP + 1).subquery("stray")
            final = union_all(
                final,
                select(
                    literal("unindexed").label("k"),
                    literal(None, type_=ranked.c.v.type).label("v"),
                    select(func.count()).select_from(capped).scalar_subquery().label("n"),
                ),
            )
        found: dict[str, list[tuple[str, int]]] = {name: [] for name in ("status", *FACET_ARRAYS)}
        unindexed: int | None = None
        for row in await budget.run(self._conn, final):
            if row.k == "unindexed":
                unindexed = int(row.n)
            else:
                found[row.k].append((row.v, int(row.n)))
        for values_ in found.values():
            values_.sort(key=lambda item: (-item[1], item[0]))
        capped_flag = unindexed is not None and unindexed > UNINDEXED_CAP
        return FacetCounts(
            found, min(unindexed, UNINDEXED_CAP) if unindexed is not None else None, capped_flag
        )

    async def summary_states(self, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """`processing` while a summarize job waits or runs; `failed` if the newest finished job
        was dead-lettered (an operator must look); otherwise `current`."""
        return await self.job_states(run_ids, SUMMARIZE_RUN)

    async def job_states(self, run_ids: Sequence[uuid.UUID], job_type: str) -> dict[uuid.UUID, str]:
        """The same states for any per-run job type whose dedupe key is `workspace:run`."""
        if not run_ids:
            return {}
        keys = {f"{self._tenant.workspace_id}:{run_id}": run_id for run_id in run_ids}
        j = t.outbox_jobs.c
        rows = await self._conn.execute(
            select(j.dedupe_key, j.status, func.max(j.updated_at).label("at"))
            .where(
                j.workspace_id == self._tenant.workspace_id,
                j.job_type == job_type,
                j.dedupe_key.in_(list(keys)),
            )
            .group_by(j.dedupe_key, j.status)
        )
        by_key: dict[str, dict[str, datetime]] = {}
        for row in rows:
            by_key.setdefault(row.dedupe_key, {})[row.status] = row.at
        states: dict[uuid.UUID, str] = {}
        for key, run_id in keys.items():
            seen = by_key.get(key, {})
            if "pending" in seen or "running" in seen:
                states[run_id] = "processing"
            elif "dead_letter" in seen and (
                "done" not in seen or seen["dead_letter"] > seen["done"]
            ):
                states[run_id] = "failed"
            else:
                states[run_id] = "current"
        return states

    async def cost_baseline(
        self,
        project_id: uuid.UUID,
        agent_slug: str,
        before: datetime,
        *,
        window_days: int,
        limit: int,
    ) -> list[tuple[datetime, Decimal]]:
        """(started_at, cost_usd) of the newest finished, summarised runs of one agent.

        Same workspace, project and agent; started before `before` and within `window_days` of it
        (relative to the run under analysis, so the answer does not depend on today's date); at
        most `limit`, newest first. Unsummarised runs (`summary_version = 0`) have no cost yet.
        """
        statement = (
            select(t.runs.c.started_at, t.runs.c.cost_usd)
            .where(
                t.runs.c.workspace_id == self._tenant.workspace_id,
                t.runs.c.project_id == project_id,
                t.runs.c.agent_slug == agent_slug,
                t.runs.c.status.in_(("SUCCESS", "FAILED", "TIMED_OUT", "BLOCKED")),
                t.runs.c.summary_version > 0,
                t.runs.c.started_at < before,
                t.runs.c.started_at >= before - timedelta(days=window_days),
            )
            .order_by(t.runs.c.started_at.desc(), t.runs.c.id.desc())
            .limit(limit)
        )
        if self._scope is not None:
            statement = statement.where(t.runs.c.project_id == self._scope)
        return [(r.started_at, r.cost_usd) for r in await self._conn.execute(statement)]
