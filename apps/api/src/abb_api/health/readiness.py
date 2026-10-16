"""What /readyz checks and how (Phase 19c step 3, ADR-169; spec 106).

`/healthz` is liveness: no I/O, always green while the event loop turns. `/readyz` answers "should
traffic be sent here": it fails (503) on a closed set of checks, each with a fixed reason code and
never an error text, a host or a connection string, and merely *reports* a few others.

Fail readiness:

* ``database``  - ``SELECT 1`` answers within its own short statement timeout. Reasons:
  ``breaker_open`` (the circuit breaker is shedding), ``timeout``, ``down``.
* ``schema``    - the database revision equals the code's alembic head. Reasons: ``behind`` (the
  code needs a migration that has not run: not ready), ``unreadable`` (the runtime role cannot
  read the revision), ``unchecked`` (the database check failed first). A database *ahead* of the
  code is ``degraded``/``ahead``, still ready: a rolling deploy migrates first, and the old
  processes must keep serving until they are replaced (migrations are additive between
  releases). If the code cannot find its migrations the check is ``degraded``/``head_unknown``.
* ``artifacts`` - the artifact directory exists (created if absent, as the store would) and a probe
  file can be written and removed. Reasons: ``missing``, ``not_writable``, ``timeout``.
* ``role``      - the periodic role check (KI-183) found no excess privilege. Production only:
  ``privileged_role`` fails; elsewhere it is ``degraded`` (local development runs as the owner).

Reported, never failing: ``worker`` (``worker_lag`` when the oldest due job has waited longer than
`WORKER_LAG_DEGRADED_SECONDS`) and ``retention`` (the configured mode as the reason).

An evaluation is cached for `TTL_SECONDS` and shared by concurrent callers (single flight), so a
load balancer, an orchestrator and a person hitting it at once cost one evaluation, and the
database is asked at most once per interval. The artifact probe is cached longer.
"""

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.core.config import Settings
from abb_api.db.breaker import BreakerOpen, DbBreaker
from abb_api.db.role_check import RoleMonitor
from abb_api.obs.catalogue import METRICS, READINESS_CHECKS, Catalogue

logger = logging.getLogger(__name__)

TTL_SECONDS: Final = 2.0
ARTIFACT_TTL_SECONDS: Final = 5.0
DATABASE_DEADLINE_SECONDS: Final = 2.5
STATEMENT_TIMEOUT_MS: Final = 2000
ARTIFACT_DEADLINE_SECONDS: Final = 2.0
WORKER_LAG_DEGRADED_SECONDS: Final = 60.0
# The same bounded look at the due jobs as the scrape sampler: oldest first, so the lag is exact.
_OLDEST_DUE = text(
    "SELECT extract(epoch FROM (now() - min(available_at))) FROM ("
    " SELECT available_at FROM outbox_jobs WHERE status = 'pending' AND available_at <= now()"
    " ORDER BY available_at LIMIT 1) AS due"
)
_REVISION = text("SELECT version_num FROM alembic_version")

Status = Literal["ok", "fail", "degraded"]


@dataclass(frozen=True)
class CheckResult:
    status: Status
    reason: str | None = None


@dataclass(frozen=True)
class Report:
    ready: bool
    checks: dict[str, CheckResult]


@dataclass(frozen=True)
class CodeRevisions:
    head: str
    known: frozenset[str]


@lru_cache(maxsize=1)
def code_revisions() -> CodeRevisions | None:
    """The alembic head of this code and every revision it knows, read once (blocking: call it in
    a thread). None when the migrations are not shipped next to the code."""
    candidates = [Path(p) for p in (os.environ.get("ABB_ALEMBIC_DIR"),) if p]
    candidates += [Path.cwd(), Path(__file__).resolve().parents[3]]
    for root in candidates:
        if (root / "alembic.ini").is_file() and (root / "migrations").is_dir():
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "migrations"))
            script = ScriptDirectory.from_config(config)
            head = script.get_current_head()
            if head is None:
                return None
            return CodeRevisions(head, frozenset(r.revision for r in script.walk_revisions()))
    return None


def probe_directory(directory: str) -> CheckResult:
    """Blocking: the directory exists and a tiny file can be created in it and removed again.

    A directory that does not exist yet is created, as the store would on its first write (a fresh
    install has none until the first upload); one that cannot be created is `missing`.
    """
    root = Path(directory)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return CheckResult("fail", "missing")
    probe = root / f".readyz-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)
        return CheckResult("fail", "not_writable")
    return CheckResult("ok")


def judge_revision(found: str | None, code: CodeRevisions | None) -> CheckResult:
    if code is None:
        return CheckResult("degraded", "head_unknown")
    if found is None:
        return CheckResult("fail", "behind")  # an empty alembic_version: nothing was migrated
    if found == code.head:
        return CheckResult("ok")
    return (
        CheckResult("fail", "behind") if found in code.known else CheckResult("degraded", "ahead")
    )


class Readiness:
    def __init__(
        self,
        engine_of: Callable[[], AsyncEngine | None],
        settings: Settings,
        *,
        breaker: DbBreaker | None = None,
        role: RoleMonitor | None = None,
        artifacts_dir: str | None = None,
        metrics: Catalogue = METRICS,
        monotonic: Callable[[], float] = time.monotonic,
        ttl_seconds: float = TTL_SECONDS,
        artifact_ttl_seconds: float = ARTIFACT_TTL_SECONDS,
    ) -> None:
        self._engine_of = engine_of
        self._settings = settings
        self._breaker = breaker
        self._role = role
        self._artifacts_dir = artifacts_dir
        self._metrics = metrics
        self._mono = monotonic
        self._ttl = ttl_seconds
        self._artifact_ttl = artifact_ttl_seconds
        self._report: Report | None = None
        self._at = float("-inf")
        self._flight: asyncio.Task[Report] | None = None
        self._artifact: tuple[float, CheckResult] | None = None
        self._last_ready: bool | None = None
        self.evaluations = 0  # for tests: how many times the checks really ran

    async def evaluate(self) -> Report:
        if self._report is not None and self._mono() - self._at < self._ttl:
            return self._report
        flight = self._flight
        if flight is None or flight.done():
            flight = self._flight = asyncio.create_task(self._run(), name="abb-readiness")
        return await asyncio.shield(flight)

    async def _run(self) -> Report:
        self.evaluations += 1
        database, schema, worker = await self._database()
        artifacts, role = await asyncio.gather(self._artifacts(), self._role_check())
        checks = {
            "database": database,
            "schema": schema,
            "artifacts": artifacts,
            "role": role,
            "worker": worker,
            "retention": CheckResult("ok", self._settings.retention_mode),
        }
        ready = all(checks[name].status != "fail" for name in READINESS_CHECKS)
        for name in READINESS_CHECKS:
            self._metrics.readiness_check.set(0.0 if checks[name].status == "fail" else 1.0, name)
        if self._last_ready is not None and ready != self._last_ready:
            self._metrics.readiness_flaps_total.inc()
            logger.warning(
                "readiness changed",
                extra={
                    "ready": ready,
                    "failing": sorted(n for n, c in checks.items() if c.status == "fail"),
                },
            )
        self._last_ready = ready
        self._report = Report(ready, checks)
        self._at = self._mono()
        self._flight = None
        return self._report

    # ------------------------------------------------------------------ the database

    async def _database(self) -> tuple[CheckResult, CheckResult, CheckResult]:
        """One connection answers the database, schema and worker checks."""
        unchecked = CheckResult("fail", "unchecked")
        engine = self._engine_of()
        if engine is None:
            return CheckResult("fail", "down"), unchecked, CheckResult("degraded", "unchecked")
        if self._breaker is not None and self._breaker.shedding:
            return (
                CheckResult("fail", "breaker_open"),
                unchecked,
                CheckResult("degraded", "unchecked"),
            )
        schema = CheckResult("fail", "unreadable")
        worker = CheckResult("degraded", "unchecked")
        try:
            async with asyncio.timeout(DATABASE_DEADLINE_SECONDS):
                async with engine.connect() as conn:
                    await conn.execute(
                        text("SELECT set_config('statement_timeout', :ms, true)"),
                        {"ms": str(STATEMENT_TIMEOUT_MS)},
                    )
                    await conn.execute(text("SELECT 1"))
                    schema, worker = await self._after_ping(conn)
        except BreakerOpen:
            return CheckResult("fail", "breaker_open"), unchecked, worker
        except TimeoutError:
            return CheckResult("fail", "timeout"), unchecked, worker
        except Exception as exc:
            logger.warning(
                "readiness database check failed", extra={"error_type": type(exc).__name__}
            )
            return CheckResult("fail", "down"), unchecked, worker
        return CheckResult("ok"), schema, worker

    async def _after_ping(self, conn: AsyncConnection) -> tuple[CheckResult, CheckResult]:
        code = await asyncio.to_thread(code_revisions)
        try:
            async with conn.begin_nested():
                found = (await conn.execute(_REVISION)).scalar()
            schema = judge_revision(found, code)
        except Exception:  # permission denied for the runtime role before migration 0105
            schema = CheckResult("fail", "unreadable")
        try:
            age = (await conn.execute(_OLDEST_DUE)).scalar()
            late = age is not None and float(age) > WORKER_LAG_DEGRADED_SECONDS
            worker = CheckResult("degraded", "worker_lag") if late else CheckResult("ok")
        except Exception:
            worker = CheckResult("degraded", "unchecked")
        return schema, worker

    # ------------------------------------------------------------------ the rest

    async def _artifacts(self) -> CheckResult:
        if self._artifacts_dir is None:
            return CheckResult("ok")
        now = self._mono()
        if self._artifact is not None and now - self._artifact[0] < self._artifact_ttl:
            return self._artifact[1]
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(probe_directory, self._artifacts_dir), ARTIFACT_DEADLINE_SECONDS
            )
        except TimeoutError:
            result = CheckResult("fail", "timeout")
        self._artifact = (now, result)
        return result

    async def _role_check(self) -> CheckResult:
        role = self._role
        if role is None or role.state == "ok":
            return CheckResult("ok")
        if role.refuses_traffic:
            return CheckResult("fail", "privileged_role")
        if role.state == "privileged":
            return CheckResult("degraded", "privileged_role")
        return CheckResult("degraded", "unverified")
