"""The event store: the one transaction that makes accepted telemetry durable (spec §66, §72).

`202` means this transaction committed. Within it:

1. in-batch repeats are collapsed (a repeat of identical content is a duplicate, a repeat with
   different content is a conflict; the first occurrence wins),
2. agents and runs are created on first sight (arrival order never matters),
3. events are inserted with ON CONFLICT DO NOTHING on (workspace_id, event_id), and the ones that
   were already stored are classified by comparing content hashes,
4. one coalesced `summarize_run` job per affected run is enqueued.

Accepted events are never updated or deleted (INV-1). Rows are written in id order everywhere so
overlapping concurrent batches cannot deadlock on each other.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, NamedTuple, Protocol

from abb_event_schema.dedup import content_hash
from abb_event_schema.event import Event
from abb_event_schema.ids import to_uuid
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.db.event_rows import event_to_row, row_to_event
from abb_api.jobs.outbox import SUMMARIZE_RUN, OutboxRepository, summarize_key
from abb_api.projects.repository import AgentRepository
from abb_api.quotas.model import QUOTA_EXCEEDED, Candidate, QuotaLimits, admit
from abb_api.quotas.repository import QuotaRepository
from abb_api.runs.repository import RunRepository, RunSeed
from abb_api.streaming import notify
from abb_api.tenancy import TenantContext

INSERT_CHUNK = 500  # 22 columns x 500 rows stays far below the 32767 bind-parameter limit

Status = Literal["accepted", "duplicate", "conflict", "rejected"]
RUN_PROJECT_MISMATCH = "RUN_PROJECT_MISMATCH"


@dataclass(frozen=True)
class EventResult:
    event_id: str
    status: Status
    code: str | None = None  # set when rejected
    quota: str | None = None  # which quota, when the code is QUOTA_EXCEEDED


class _Admitted(NamedTuple):
    candidates: list[int]  # the batch's new events that fit, in batch order
    project: uuid.UUID
    run_owners: dict[uuid.UUID, uuid.UUID]  # runs that exist, with their project
    known_agents: set[str]  # slugs the project already has (no row to create or look up)


@dataclass(frozen=True)
class QuotaPolicy:
    """Turns quota checking on for one ingest: the environment defaults and the UTC day to count."""

    defaults: QuotaLimits
    day: date
    lock_timeout_ms: int = 5000


@dataclass(frozen=True)
class IngestOutcome:
    """Per-event results, in input order."""

    results: list[EventResult] = field(default_factory=list)

    def count(self, status: Status) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def accepted(self) -> int:
        return self.count("accepted")

    @property
    def duplicates(self) -> int:
        return self.count("duplicate")

    @property
    def conflicts(self) -> int:
        return self.count("conflict")

    @property
    def rejected(self) -> int:
        return self.count("rejected")


class EventStore(Protocol):
    """Storage contract (INV-7): ingestion depends on this, not on PostgreSQL."""

    async def ingest(self, events: Sequence[Event]) -> IngestOutcome: ...

    async def load_run(
        self, run_id: uuid.UUID, limit: int | None = None, ordering_mode: str | None = None
    ) -> list[Event]: ...


class PgEventStore:
    def __init__(
        self,
        conn: AsyncConnection,
        tenant: TenantContext,
        *,
        summary_delay: timedelta = timedelta(0),
        quota: QuotaPolicy | None = None,
    ) -> None:
        self._conn = conn
        self._tenant = tenant
        self._summary_delay = summary_delay
        self._quota = quota

    async def ingest(self, events: Sequence[Event]) -> IngestOutcome:
        if not events:
            return IngestOutcome()
        workspace = self._tenant.workspace_id
        for event in events:
            if to_uuid(event.workspace_id) != workspace:
                # A programming error, not bad input: the service binds every event to the tenant.
                raise ValueError("event does not belong to this store's tenant")

        digests = [bytes.fromhex(content_hash(e)) for e in events]
        status: list[Status | None] = [None] * len(events)
        codes: dict[int, str] = {}

        # 1. collapse repeats inside the batch; the first occurrence of an id is the candidate
        first_index: dict[str, int] = {}
        for i, event in enumerate(events):
            seen = first_index.setdefault(event.event_id, i)
            if seen != i:
                status[i] = "duplicate" if digests[seen] == digests[i] else "conflict"
        candidates = list(first_index.values())

        # 1b. quotas (ADR-154): events already stored are classified here (a replay is never
        # refused and never counted); of the new ones only those that fit continue
        quota_names: dict[int, str] = {}
        quota_project: uuid.UUID | None = None
        known_owners: dict[uuid.UUID, uuid.UUID] | None = None
        known_agents: set[str] = set()
        if self._quota is not None and candidates:
            admitted = await self._admit(events, candidates, digests, status, codes, quota_names)
            candidates, quota_project = admitted.candidates, admitted.project
            known_owners, known_agents = admitted.run_owners, admitted.known_agents

        # 2. agents (and their versions), then runs: both on first sight, both in a stable order
        versions: dict[tuple[uuid.UUID, str], set[str]] = {}
        for i in candidates:
            key = (to_uuid(events[i].project_id), events[i].agent_id)
            versions.setdefault(key, set())
            if events[i].agent_version:
                versions[key].add(str(events[i].agent_version))
        agents = AgentRepository(self._conn, self._tenant)
        for (project_id, slug), fingerprints in sorted(
            versions.items(), key=lambda item: (item[0][0].bytes, item[0][1])
        ):
            if slug in known_agents and not fingerprints:
                continue  # the agent exists and nothing needs its id: two statements saved
            agent = await agents.ensure(project_id=project_id, slug=slug)
            for fingerprint in sorted(fingerprints):
                await agents.ensure_version(agent_id=agent.id, fingerprint=fingerprint)

        seeds: dict[uuid.UUID, RunSeed] = {}
        for i in candidates:
            event = events[i]
            run_id = to_uuid(event.run_id)
            earliest = seeds.get(run_id)
            if earliest is None or event.occurred_at < earliest.started_at:
                seeds[run_id] = RunSeed(
                    run_id=run_id,
                    project_id=to_uuid(event.project_id),
                    trace_id=to_uuid(event.trace_id),
                    agent_slug=event.agent_id,
                    started_at=event.occurred_at,
                )
        owners, created_runs = await RunRepository(self._conn, self._tenant).ensure_runs_created(
            list(seeds.values()), known_owners=known_owners
        )

        insertable: list[int] = []
        for i in candidates:
            if owners[to_uuid(events[i].run_id)] != to_uuid(events[i].project_id):
                status[i], codes[i] = "rejected", RUN_PROJECT_MISMATCH  # a run has one project
            else:
                insertable.append(i)

        # 3. insert; whatever was not inserted already existed
        insertable.sort(key=lambda i: to_uuid(events[i].event_id).bytes)
        inserted: set[uuid.UUID] = set()
        for start in range(0, len(insertable), INSERT_CHUNK):
            chunk = [event_to_row(events[i]) for i in insertable[start : start + INSERT_CHUNK]]
            returned = await self._conn.execute(
                insert(t.events)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=["workspace_id", "event_id"])
                .returning(t.events.c.event_id)
            )
            inserted.update(r.event_id for r in returned)
        if self._quota is not None and quota_project is not None:
            # Counted from what really went in, under the project lock taken in `_admit`.
            await QuotaRepository(self._conn, self._tenant).add_usage(
                quota_project, self._quota.day, runs=len(created_runs), events=len(inserted)
            )
        existing = [i for i in insertable if to_uuid(events[i].event_id) not in inserted]
        stored = await self._stored_hashes([to_uuid(events[i].event_id) for i in existing])
        for i in insertable:
            event_id = to_uuid(events[i].event_id)
            if event_id in inserted:
                status[i] = "accepted"
            else:
                status[i] = "duplicate" if stored[event_id] == digests[i] else "conflict"

        # 4. one coalesced job per run that gained events, and one wake-up for its live streams
        outbox = OutboxRepository(self._conn, self._tenant)
        for run_id in sorted(
            {to_uuid(events[i].run_id) for i in insertable if status[i] == "accepted"},
            key=lambda r: r.bytes,
        ):
            await outbox.enqueue(
                job_type=SUMMARIZE_RUN,
                dedupe_key=summarize_key(workspace, run_id),
                payload={"run_id": str(run_id)},
                delay=self._summary_delay,
            )
            # Delivered only on commit (ADR-022): a rolled-back batch wakes nobody.
            await self._conn.execute(
                select(
                    func.pg_notify(
                        notify.CHANNEL, notify.encode((self._tenant.workspace_id, run_id))
                    )
                )
            )

        results = []
        for i, event in enumerate(events):
            assert status[i] is not None
            results.append(
                EventResult(event.event_id, status[i], codes.get(i), quota_names.get(i))  # type: ignore[arg-type]
            )
        return IngestOutcome(results)

    async def _admit(
        self,
        events: Sequence[Event],
        candidates: list[int],
        digests: list[bytes],
        status: list[Status | None],
        codes: dict[int, str],
        quota_names: dict[int, str],
    ) -> _Admitted:
        """Apply the project's quotas to the batch's distinct events.

        Sets the final status of every event that needs no storage work (already stored, wrong
        project, refused) and returns the ones to store, the project, and the owners of the runs
        that exist (so run creation need not ask again). The project lock is taken before any
        counter is read.
        """
        assert self._quota is not None
        policy = self._quota
        projects = {to_uuid(events[i].project_id) for i in candidates}
        if len(projects) != 1:
            raise ValueError("quota-checked ingestion takes the events of one project")
        project_id = next(iter(projects))
        quotas = QuotaRepository(self._conn, self._tenant, lock_timeout_ms=policy.lock_timeout_ms)

        # The lock first, then one read of everything the decision needs (see `QuotaRepository`).
        # A batch of replays takes the lock too, but only for the length of that one read.
        if not await quotas.lock_project_row(project_id):
            raise ValueError("event does not belong to a project of this tenant")
        snapshot = await quotas.snapshot(
            project_id,
            policy.day,
            event_ids=[to_uuid(events[i].event_id) for i in candidates],
            run_ids=list({to_uuid(events[i].run_id) for i in candidates}),
            slugs=sorted({events[i].agent_id for i in candidates}),
        )
        if snapshot is None:
            raise ValueError("event does not belong to a project of this tenant")
        stored, owners, state = snapshot.stored, snapshot.run_owners, snapshot.state

        fresh: list[Candidate] = []
        for i in candidates:
            event_id, run_id = to_uuid(events[i].event_id), to_uuid(events[i].run_id)
            if owners.get(run_id, project_id) != project_id:
                status[i], codes[i] = "rejected", RUN_PROJECT_MISMATCH  # a run has one project
            elif event_id in stored:
                status[i] = "duplicate" if stored[event_id] == digests[i] else "conflict"
            else:
                fresh.append(Candidate(i, events[i].agent_id, run_id))
        if not fresh:
            return _Admitted([], project_id, owners, snapshot.known_agents)

        limits = state.limits(policy.defaults)
        slugs = {c.agent for c in fresh}
        known = snapshot.known_agents
        agents_used = 0
        if slugs - known:  # only a new slug needs the count
            agents_used = await quotas.count_agents(project_id, limits.agents)
        decision = admit(
            fresh,
            limits=limits,
            agents_used=agents_used,
            runs_today=state.runs_today,
            events_today=state.events_today,
            known_agents=known,
            known_runs=owners.keys(),
        )
        for i, quota in decision.refused.items():
            status[i], codes[i], quota_names[i] = "rejected", QUOTA_EXCEEDED, quota.value
        return _Admitted(decision.accepted, project_id, owners, known)

    async def load_run(
        self, run_id: uuid.UUID, limit: int | None = None, ordering_mode: str | None = None
    ) -> list[Event]:
        """Stored events of one run (the summarizer's input), in no particular order.

        With `limit` (the detector job's bound, KI-016/022) at most that many are read: the first
        ones in the run's canonical order, which SQL applies with the *full* key of the mode
        (`ordering_mode`: sequence, occurred_at, received_at, event_id, or occurred_at, sequence,
        received_at, event_id), so ties never make the capped set differ from the prefix of
        `sort_events`. Without `limit` nothing is ordered and nothing is capped.
        """
        c = t.events.c
        statement = select(t.events).where(
            c.workspace_id == self._tenant.workspace_id, c.run_id == run_id
        )
        if limit is not None:
            keys: tuple[Any, ...]
            if ordering_mode == "sequence":
                keys = (c.sequence, c.occurred_at, c.received_at, c.event_id)
            else:
                keys = (c.occurred_at, func.coalesce(c.sequence, 0), c.received_at, c.event_id)
            statement = statement.order_by(*keys).limit(limit)
        rows = await self._conn.execute(statement)
        return [row_to_event(row) for row in rows]

    async def _stored_hashes(self, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, bytes]:
        if not event_ids:
            return {}
        rows = await self._conn.execute(
            select(t.events.c.event_id, t.events.c.content_hash).where(
                t.events.c.workspace_id == self._tenant.workspace_id,
                t.events.c.event_id.in_(event_ids),
            )
        )
        return {r.event_id: bytes(r.content_hash) for r in rows}
