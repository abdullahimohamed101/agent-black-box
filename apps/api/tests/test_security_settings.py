"""Per-project security settings: defaults, scoping, validation and the audit trail (ADR-130)."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.audit.repository import AuditRepository, AuditRow
from abb_api.core.domain import NotFoundError
from abb_api.db import tables as t
from abb_api.jobs.outbox import DETECT_FINDINGS
from abb_api.security.settings import (
    DEFAULT_CAPTURE_MODE,
    MAX_ALLOWLIST_ENTRIES,
    Actor,
    CaptureMode,
    InvalidSettingsError,
    PgSecuritySettingsStore,
    ReanalysisPolicy,
    SecuritySettings,
    SecuritySettingsService,
    audit_details,
    normalize_detector_ids,
    normalize_host_patterns,
)
from tests.conftest import runtime_url
from tests.factories import make_run
from tests.ingest_helpers import Tenant, make_tenant

OWNER = Actor("user", "user:usr_owner")


async def two(engine: AsyncEngine) -> tuple[Tenant, Tenant]:
    """Workspaces with default settings (no row): the product default, METADATA_ONLY."""
    a = await make_tenant(engine, "acme", projects=("alpha", "beta"), capture_mode=None)
    b = await make_tenant(engine, "globex", projects=("alpha",), capture_mode=None)
    return a, b


async def audit_rows(engine: AsyncEngine, tenant: Tenant) -> list[AuditRow]:
    async with engine.connect() as conn:
        return await AuditRepository(conn, tenant.context).recent(100)


# --------------------------------------------------------------------- defaults and storage


async def test_a_project_without_a_row_has_the_safe_defaults(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]
    async with engine.connect() as conn:
        settings = await PgSecuritySettingsStore(conn, a.context).get(project)
        assert settings == SecuritySettings(project)
        assert settings.capture_mode is CaptureMode.METADATA_ONLY is DEFAULT_CAPTURE_MODE
        assert settings.muted_detectors == () and settings.destination_allowlist == ()
        assert settings.updated_at is None and settings.updated_by is None
        assert await PgSecuritySettingsStore(conn, a.context).list() == []


async def test_upsert_creates_a_row_and_changes_only_the_given_fields(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]
    async with engine.begin() as conn:
        store = PgSecuritySettingsStore(conn, a.context)
        created = await store.upsert(
            project,
            updated_by="user:one",
            capture_mode=CaptureMode.FULL,
            muted_detectors=["sec.risky_git", "sec.destructive_command"],
            destination_allowlist=["API.Example.com", "*.example.org"],
        )
        assert created.capture_mode is CaptureMode.FULL
        assert created.muted_detectors == ("sec.destructive_command", "sec.risky_git")
        assert created.destination_allowlist == ("*.example.org", "api.example.com")
        assert created.updated_by == "user:one" and created.updated_at is not None

        only_mode = await store.upsert(
            project, updated_by="user:two", capture_mode=CaptureMode.DISABLED
        )
        assert only_mode.capture_mode is CaptureMode.DISABLED
        assert only_mode.muted_detectors == created.muted_detectors
        assert only_mode.destination_allowlist == created.destination_allowlist
        assert only_mode.updated_by == "user:two"

        cleared = await store.upsert(project, updated_by="user:two", muted_detectors=[])
        assert cleared.muted_detectors == () and cleared.capture_mode is CaptureMode.DISABLED
        assert [s.project_id for s in await store.list()] == [project]
        assert await store.get(project) == cleared


async def test_settings_are_scoped_to_the_workspace_and_the_project(engine: AsyncEngine) -> None:
    a, b = await two(engine)
    alpha, beta = a.project_uuids["alpha"], a.project_uuids["beta"]
    async with engine.begin() as conn:
        await PgSecuritySettingsStore(conn, a.context).upsert(
            alpha,
            updated_by="x",
            capture_mode=CaptureMode.FULL,
            destination_allowlist=["a.example.com"],
        )
    async with engine.connect() as conn:
        mine = PgSecuritySettingsStore(conn, a.context)
        theirs = PgSecuritySettingsStore(conn, b.context)
        assert (await mine.get(alpha)).capture_mode is CaptureMode.FULL
        assert (await mine.get(beta)).capture_mode is CaptureMode.METADATA_ONLY  # other project
        # another workspace asking about this project id sees nothing of it
        assert await theirs.get(alpha) == SecuritySettings(alpha)
        assert await theirs.list() == []
        assert [s.project_id for s in await mine.list()] == [alpha]


async def test_a_workspace_cannot_write_settings_for_another_workspaces_project(
    engine: AsyncEngine,
) -> None:
    a, b = await two(engine)
    foreign = a.project_uuids["alpha"]
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):  # the composite foreign key, even if code is wrong
            await PgSecuritySettingsStore(conn, b.context).upsert(
                foreign, updated_by="x", capture_mode=CaptureMode.FULL
            )
    service = SecuritySettingsService(engine)
    with pytest.raises(NotFoundError):
        await service.update(b.context, foreign, OWNER, capture_mode=CaptureMode.FULL)
    with pytest.raises(NotFoundError):
        await service.update(a.context, uuid.uuid4(), OWNER, capture_mode=CaptureMode.FULL)
    assert await audit_rows(engine, a) == [] and await audit_rows(engine, b) == []


async def test_the_database_rejects_values_the_code_would_not_write(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]
    base = {"w": a.context.workspace_id, "p": project}
    statements = [
        "capture_mode = 'ALL'",
        "muted_detectors = '\"x\"'::jsonb",
        "destination_allowlist = '{}'::jsonb",
        "updated_by = repeat('x', 129)",
    ]
    for assignment in statements:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO project_security_settings (workspace_id, project_id, updated_by) "
                    "VALUES (:w, :p, 'x')"
                ),
                base,
            )
        async with engine.connect() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        f"UPDATE project_security_settings SET {assignment} "  # noqa: S608
                        "WHERE workspace_id = :w AND project_id = :p"
                    ),
                    base,
                )
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM project_security_settings"))


async def test_the_runtime_role_can_read_and_write_settings(
    engine: AsyncEngine, database_url: str, runtime_database_url: str
) -> None:
    a, _ = await two(engine)
    runtime = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    try:
        await SecuritySettingsService(runtime).update(
            a.context, a.project_uuids["alpha"], OWNER, capture_mode=CaptureMode.FULL
        )
        got = await SecuritySettingsService(runtime).get(a.context, a.project_uuids["alpha"])
        assert got.capture_mode is CaptureMode.FULL
    finally:
        await runtime.dispose()


# ------------------------------------------------------------------------------ validation


def test_detector_ids_are_normalised_and_validated() -> None:
    assert normalize_detector_ids(["sec.b", "sec.a", "sec.a"]) == ("sec.a", "sec.b")
    for bad in [
        "secret_file_read",
        "sec.",
        "sec.Upper",
        "sec.a b",
        "SEC.x",
        "sec.x\n",
        "sec." + "a" * 61,
    ]:
        with pytest.raises(InvalidSettingsError):
            normalize_detector_ids([bad])
    with pytest.raises(InvalidSettingsError):
        normalize_detector_ids([f"sec.d{i}" for i in range(65)])


def test_host_patterns_are_normalised_and_validated() -> None:
    assert normalize_host_patterns([" API.Example.com. ", "*.example.org", "api.example.com"]) == (
        "*.example.org",
        "api.example.com",
    )
    assert normalize_host_patterns(["localhost", "10.0.0.1", "a-b.c-d.io"]) == (
        "10.0.0.1",
        "a-b.c-d.io",
        "localhost",
    )
    bad = [
        "https://example.com", "example.com/path", "example.com:8080", "*", "*example.com",
        "exa mple.com", "-bad.com", "bad-.com", "a..b", "", "x" * 101, "ex\nample.com",
        "*.*.example.com", "user@example.com",
    ]  # fmt: skip
    for entry in bad:
        with pytest.raises(InvalidSettingsError):
            normalize_host_patterns([entry])
    with pytest.raises(InvalidSettingsError):
        normalize_host_patterns([f"h{i}.example.com" for i in range(MAX_ALLOWLIST_ENTRIES + 1)])


async def test_invalid_values_change_nothing_and_leave_no_audit_row(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    service = SecuritySettingsService(engine)
    project = a.project_uuids["alpha"]
    with pytest.raises(InvalidSettingsError):
        await service.update(a.context, project, OWNER, destination_allowlist=["https://x.io"])
    with pytest.raises(InvalidSettingsError):
        await service.update(a.context, project, OWNER, muted_detectors=["nope"])
    assert (await service.get(a.context, project)).updated_at is None
    assert await audit_rows(engine, a) == []


# ----------------------------------------------------------------------------------- audit


async def test_every_change_writes_one_audit_entry_naming_what_changed(
    engine: AsyncEngine,
) -> None:
    a, b = await two(engine)
    project = a.project_uuids["alpha"]
    service = SecuritySettingsService(engine)
    await service.update(
        a.context, project, OWNER, capture_mode=CaptureMode.FULL, muted_detectors=["sec.risky_git"]
    )
    await service.update(
        a.context, project, Actor("api_key", "key:k1"), destination_allowlist=["a.example.com"]
    )
    rows = list(reversed(await audit_rows(engine, a)))
    assert [r.action for r in rows] == ["security.settings.update"] * 2
    first, second = rows
    assert (first.actor_kind, first.actor_id) == ("user", "user:usr_owner")
    assert (first.resource_kind, first.resource_id.startswith("prj_")) == ("project", True)  # type: ignore[union-attr]
    assert first.details == {
        "fields": ["capture_mode", "muted_detectors"],
        "capture_mode_from": "METADATA_ONLY",
        "capture_mode_to": "FULL",
        "muted_added": ["sec.risky_git"],
        "muted_removed": [],
        "muted_added_count": 1,
        "muted_removed_count": 0,
    }
    assert (second.actor_kind, second.actor_id) == ("api_key", "key:k1")
    assert second.details["fields"] == ["destination_allowlist"]
    assert second.details["allowlist_added"] == ["a.example.com"]
    assert (await service.get(a.context, project)).updated_by == "key:k1"
    assert await audit_rows(engine, b) == []  # another workspace sees none of it


async def test_a_request_that_changes_nothing_writes_nothing(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]
    service = SecuritySettingsService(engine)
    same_as_default = await service.update(
        a.context, project, OWNER, capture_mode=CaptureMode.METADATA_ONLY
    )
    assert same_as_default.updated_at is None  # no row was created for a no-op
    await service.update(a.context, project, OWNER, capture_mode=CaptureMode.FULL)
    before = await service.get(a.context, project)
    again = await service.update(
        a.context,
        project,
        Actor("user", "user:other"),
        capture_mode=CaptureMode.FULL,
        muted_detectors=[],
    )
    assert again == before  # same row, same updated_at/updated_by
    assert len(await audit_rows(engine, a)) == 1


async def test_a_failed_audit_write_rolls_the_change_back(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]

    async def broken(self: AuditRepository, entry: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(AuditRepository, "append", broken)
    with pytest.raises(RuntimeError):
        await SecuritySettingsService(engine).update(
            a.context, project, OWNER, capture_mode=CaptureMode.FULL
        )
    monkeypatch.undo()
    assert (await SecuritySettingsService(engine).get(a.context, project)).updated_at is None


async def test_the_largest_allowed_change_fits_the_audit_details_limits(
    engine: AsyncEngine,
) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]
    wide = [f"{'h' * 45}.{'g' * 45}.n{i:03d}.io" for i in range(MAX_ALLOWLIST_ENTRIES)]
    service = SecuritySettingsService(engine)
    await service.update(
        a.context,
        project,
        OWNER,
        capture_mode=CaptureMode.DISABLED,
        muted_detectors=[f"sec.{'d' * 55}{i:02d}" for i in range(64)],
        destination_allowlist=wide,
    )
    await service.update(a.context, project, OWNER, muted_detectors=[], destination_allowlist=[])
    rows = list(reversed(await audit_rows(engine, a)))
    assert len(rows) == 2  # clean_details accepted both: nothing was silently dropped
    assert rows[0].details["allowlist_added_count"] == MAX_ALLOWLIST_ENTRIES
    assert len(rows[0].details["allowlist_added"]) == 4
    assert json.dumps(rows[1].details)  # the removal is bounded the same way


def test_audit_details_never_carry_the_listed_values_beyond_a_few() -> None:
    project = uuid.uuid4()
    before = SecuritySettings(project, destination_allowlist=tuple(f"h{i}.io" for i in range(10)))
    after = SecuritySettings(project, destination_allowlist=())
    details = audit_details(before, after)
    assert details["allowlist_removed_count"] == 10 and len(details["allowlist_removed"]) == 4


async def test_concurrent_changes_are_applied_one_after_another(engine: AsyncEngine) -> None:
    a, _ = await two(engine)
    project = a.project_uuids["alpha"]
    service = SecuritySettingsService(engine)
    await asyncio.gather(
        *(
            service.update(a.context, project, OWNER, muted_detectors=[f"sec.d{i}"])
            for i in range(6)
        )
    )
    rows = list(reversed(await audit_rows(engine, a)))
    assert len(rows) == 6
    # each entry's "added" is consistent with a serial order: the final state is the last writer's
    final = await service.get(a.context, project)
    last = rows[-1].details
    assert list(final.muted_detectors) == last["muted_added"]


def test_a_host_name_may_be_as_long_as_a_dns_name_and_no_longer() -> None:
    """RFC 1035: 253 characters in labels of at most 63 (review P3-5); `*.` is on top of that."""
    labels = ["a" * 63, "b" * 63, "c" * 63, "d" * 61]
    longest = ".".join(labels)
    assert len(longest) == 253
    assert normalize_host_patterns([longest]) == (longest,)
    assert normalize_host_patterns(["*." + longest]) == ("*." + longest,)
    with pytest.raises(InvalidSettingsError):
        normalize_host_patterns([longest + "e"])  # 254 characters
    with pytest.raises(InvalidSettingsError):
        normalize_host_patterns(["*." + longest + "e"])


def test_a_non_ascii_host_is_refused_with_a_punycode_hint() -> None:
    for entry in ("bücher.example", "例え.jp", "*.münchen.de"):
        with pytest.raises(InvalidSettingsError) as caught:
            normalize_host_patterns([entry])
        assert "punycode" in str(caught.value) and "xn--" in str(caught.value)
        assert entry not in str(caught.value)  # the message never echoes the value
    assert normalize_host_patterns(["xn--bcher-kva.example"]) == ("xn--bcher-kva.example",)


def test_the_audit_row_of_long_allowlist_entries_stays_within_the_audit_limits() -> None:
    from abb_api.audit.repository import clean_details

    longs = [f"{i}{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 58}" for i in range(4)]
    before = SecuritySettings(uuid.uuid4())
    after = SecuritySettings(before.project_id, destination_allowlist=tuple(longs))
    clean_details(audit_details(before, after))  # raises ValueError when a value is too long
    clean_details(audit_details(after, before))
    clean_details(
        audit_details(after, SecuritySettings(before.project_id, muted_detectors=("sec.a",)))
    )


# ----------------------------------------------- re-analysis waves are rate limited (review P3-4)


async def seconds_until_pending_job(engine: AsyncEngine, run_id: uuid.UUID) -> float | None:
    async with engine.connect() as conn:
        due = await conn.scalar(
            select(func.extract("epoch", t.outbox_jobs.c.available_at - func.now())).where(
                t.outbox_jobs.c.job_type == DETECT_FINDINGS,
                t.outbox_jobs.c.status == "pending",
                t.outbox_jobs.c.payload["run_id"].astext == str(run_id),
            )
        )
    return None if due is None else float(due)


async def finish_jobs(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:  # what the worker does once it has run them
        await conn.execute(update(t.outbox_jobs).values(status="done"))


async def project_with_a_run(engine: AsyncEngine, slug: str) -> tuple[Tenant, uuid.UUID, uuid.UUID]:
    a = await make_tenant(engine, slug, projects=("alpha", "beta"), capture_mode=None)
    project = a.project_uuids["alpha"]
    async with engine.begin() as conn:
        run = await make_run(conn, a.context.workspace_id, project, started_at=datetime.now(UTC))
    return a, project, run


async def test_a_second_wave_within_the_cooldown_is_deferred_not_dropped(
    engine: AsyncEngine,
) -> None:
    """A scripted admin toggling a mute cannot keep the worker busy: after the first wave ran, the
    next one is queued, but not due until the cooldown after the previous change has passed."""
    a, project, run = await project_with_a_run(engine, "acme")
    service = SecuritySettingsService(
        engine, ReanalysisPolicy(delay_seconds=1.0, cooldown_seconds=3600.0)
    )
    await service.update(a.context, project, OWNER, muted_detectors=["sec.risky_git"])
    first = await seconds_until_pending_job(engine, run)
    assert first is not None and first <= 1.5  # the first wave is due after the debounce only
    await finish_jobs(engine)  # the worker ran it

    await service.update(a.context, project, OWNER, muted_detectors=[])
    second = await seconds_until_pending_job(engine, run)
    assert second is not None and 3000 < second <= 3601  # queued, due when the cooldown ends

    # edits while a job is pending coalesce into it: still one pending job
    await service.update(a.context, project, OWNER, muted_detectors=["sec.risky_git"])
    async with engine.connect() as conn:
        pending = await conn.scalar(
            select(func.count())
            .select_from(t.outbox_jobs)
            .where(t.outbox_jobs.c.status == "pending")
        )
    assert pending == 1


async def test_the_cooldown_is_per_project_and_a_mode_change_does_not_start_it(
    engine: AsyncEngine,
) -> None:
    a, project, run = await project_with_a_run(engine, "acme")
    other = a.project_uuids["beta"]
    async with engine.begin() as conn:
        other_run = await make_run(
            conn, a.context.workspace_id, other, started_at=datetime.now(UTC)
        )
    service = SecuritySettingsService(
        engine, ReanalysisPolicy(delay_seconds=1.0, cooldown_seconds=3600.0)
    )
    await service.update(a.context, project, OWNER, capture_mode=CaptureMode.FULL)  # no wave
    await service.update(a.context, project, OWNER, muted_detectors=["sec.risky_git"])
    first = await seconds_until_pending_job(engine, run)
    assert first is not None and first <= 1.5  # the mode change did not start a cooldown
    await finish_jobs(engine)
    await service.update(a.context, other, OWNER, muted_detectors=["sec.risky_git"])
    sibling = await seconds_until_pending_job(engine, other_run)
    assert sibling is not None and sibling <= 1.5  # another project is not held back


async def test_a_zero_cooldown_switches_the_rate_limit_off(engine: AsyncEngine) -> None:
    a, project, run = await project_with_a_run(engine, "acme")
    service = SecuritySettingsService(
        engine, ReanalysisPolicy(delay_seconds=1.0, cooldown_seconds=0.0)
    )
    await service.update(a.context, project, OWNER, muted_detectors=["sec.risky_git"])
    await finish_jobs(engine)
    await service.update(a.context, project, OWNER, muted_detectors=[])
    again = await seconds_until_pending_job(engine, run)
    assert again is not None and again <= 1.5
