"""Use cases behind the policy routes: resolution of scopes, history, diff, validation, publish
and the dry run. Content never leaves: a dry run returns counts and ids."""

import asyncio
import difflib
import uuid

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.authz.principal import Principal
from abb_api.core.domain import NotFoundError
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import parse_public_id, public_id
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, retry_after_seconds
from abb_api.policies import schemas as s
from abb_api.policies.dryrun import DryRunLimits, DryRunResult, evaluate_events
from abb_api.policies.mapping import MAPPED_EVENT_TYPES
from abb_api.policies.model import DecisionKind
from abb_api.policies.parser import Issue, parse_policy
from abb_api.policies.service import (
    PolicyInvalidError,
    VersionConflictError,
    publish_policy,
)
from abb_api.policies.store import PgPolicyStore, PolicyVersion, StoredPolicy
from abb_api.projects.access import project_not_found
from abb_api.projects.repository import ProjectRepository
from abb_api.runs.event_queries import EventQueries
from abb_api.runs.repository import RunRepository
from abb_api.security.settings import Actor

MAX_DIFF_LINES = 2000  # per side; a policy has at most 200 rules
MAX_DIFF_BYTES = 64 * 1024
MAX_HISTORY_PAGE = 100
DRY_RUN_STATEMENT_TIMEOUT_MS = 8000


def policy_invalid(issues: tuple[Issue, ...]) -> AppError:
    return AppError(
        "POLICY_INVALID",
        "The policy document is not valid.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"issues": [_issue(i).model_dump() for i in issues]},
    )


def version_not_found() -> AppError:
    return AppError(
        "POLICY_VERSION_NOT_FOUND",
        "Policy version not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def _issue(issue: Issue) -> s.IssueOut:
    return s.IssueOut(line=issue.line, column=issue.column, code=issue.code, message=issue.message)


def _rate_limited(wait: float) -> AppError:
    seconds = retry_after_seconds(wait)
    return AppError(
        "RATE_LIMITED",
        "Policy changes are limited per workspace; try again shortly.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def _version_out(row: PolicyVersion) -> s.PolicyVersionOut:
    return s.PolicyVersionOut(
        scope="workspace" if row.project_id is None else "project",
        project_id=public_id(IdKind.PROJECT, row.project_id) if row.project_id else None,
        version=row.version,
        document_sha256=row.document_sha256,
        rule_count=row.rule_count,
        created_at=row.created_at,
        created_by=row.created_by,
        comment=row.comment,
    )


def _detail(row: StoredPolicy) -> s.PolicyVersionDetail:
    return s.PolicyVersionDetail(**_version_out(row).model_dump(), document=row.document_text)


class PolicyApiService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        writes_per_minute: int = 30,
        validations_per_minute: int = 120,
        dry_runs_per_minute: int = 20,
        limits: DryRunLimits = DryRunLimits(),  # noqa: B008
    ) -> None:
        self._engine = engine
        self._limits = limits
        self._limiters = {
            kind: InMemoryRateLimiter(
                events_per_second=per_minute / 60,
                burst_events=per_minute,
                bytes_per_second=1.0,
                burst_bytes=1,
            )
            for kind, per_minute in (
                ("write", writes_per_minute),
                ("validate", validations_per_minute),
                ("dry_run", dry_runs_per_minute),
            )
        }

    # ------------------------------------------------------------------ helpers

    async def _scope(
        self, conn: AsyncConnection, principal: Principal, project_id: str | None
    ) -> uuid.UUID | None:
        """The project of a request (None: the workspace base). One that is not in the caller's
        workspace is the same 404 as one that does not exist."""
        if project_id is None:
            return None
        parsed = parse_public_id(IdKind.PROJECT, project_id)
        if parsed is None or await ProjectRepository(conn, principal.tenant).get(parsed) is None:
            raise project_not_found()
        return parsed

    def _throttle(self, principal: Principal, kind: str) -> None:
        wait = self._limiters[kind].acquire(str(principal.workspace_id), events=1, bytes_=0)
        if wait is not None:
            raise _rate_limited(wait)

    # ------------------------------------------------------------------ reads

    async def current(self, principal: Principal, project_id: str | None) -> s.CurrentPolicyOut:
        async with self._engine.connect() as conn:
            scope = await self._scope(conn, principal, project_id)
            source, stored = await PgPolicyStore(conn, principal.tenant).effective(scope)
        return s.CurrentPolicyOut(
            project_id=project_id,
            source=source,
            policy=_detail(stored) if stored else None,
        )

    async def history(
        self,
        principal: Principal,
        project_id: str | None,
        *,
        limit: int,
        before_version: int | None,
    ) -> s.PolicyHistory:
        async with self._engine.connect() as conn:
            scope = await self._scope(conn, principal, project_id)
            rows = await PgPolicyStore(conn, principal.tenant).history(
                scope, limit=limit + 1, before_version=before_version
            )
        page = rows[:limit]
        return s.PolicyHistory(
            items=[_version_out(r) for r in page],
            next_before_version=page[-1].version if len(rows) > limit else None,
        )

    async def version(
        self, principal: Principal, project_id: str | None, version: int
    ) -> s.PolicyVersionDetail:
        async with self._engine.connect() as conn:
            scope = await self._scope(conn, principal, project_id)
            stored = await PgPolicyStore(conn, principal.tenant).get(scope, version)
        if stored is None:
            raise version_not_found()
        return _detail(stored)

    async def diff(
        self, principal: Principal, project_id: str | None, from_version: int, to_version: int
    ) -> s.DiffOut:
        async with self._engine.connect() as conn:
            scope = await self._scope(conn, principal, project_id)
            store = PgPolicyStore(conn, principal.tenant)
            old = await store.get(scope, from_version)
            new = await store.get(scope, to_version)
        if old is None or new is None:
            raise version_not_found()
        old_lines = old.document_text.splitlines()
        new_lines = new.document_text.splitlines()
        if max(len(old_lines), len(new_lines)) > MAX_DIFF_LINES:
            raise AppError(
                "POLICY_TOO_LARGE_TO_DIFF",
                "These versions are too long to compare.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
            )
        text_out = "\n".join(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"v{from_version}",
                tofile=f"v{to_version}",
                lineterm="",
            )
        )
        truncated = len(text_out.encode("utf-8")) > MAX_DIFF_BYTES
        if truncated:
            text_out = text_out.encode("utf-8")[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
        return s.DiffOut(
            project_id=project_id,
            from_version=from_version,
            to_version=to_version,
            diff=text_out,
            truncated=truncated,
        )

    # ------------------------------------------------------------------ validate and publish

    def validate(self, principal: Principal, body: s.ValidateIn) -> s.ValidateOut:
        self._throttle(principal, "validate")
        result = parse_policy(body.document)
        return s.ValidateOut(
            valid=result.policy is not None,
            issues=[_issue(i) for i in result.issues],
            rule_count=len(result.policy.rules) if result.policy else None,
        )

    async def publish(self, principal: Principal, body: s.PublishIn) -> s.PolicyVersionDetail:
        self._throttle(principal, "write")
        parsed = parse_policy(body.document)
        if parsed.policy is None:  # before any connection: a bad draft costs no transaction
            raise policy_invalid(parsed.issues)
        try:
            async with self._engine.begin() as conn:
                scope = await self._scope(conn, principal, body.project_id)
                stored = await publish_policy(
                    conn,
                    principal.tenant,
                    scope,
                    Actor.of(principal),
                    body.document,
                    comment=body.comment,
                    base_version=body.base_version,
                )
        except PolicyInvalidError as error:
            raise policy_invalid(error.issues) from None
        except VersionConflictError as error:
            raise AppError(
                "POLICY_VERSION_CONFLICT",
                "The policy was changed by someone else; reload it before publishing.",
                category=ErrorCategory.CONFLICT,
                status_code=409,
                details={"current_version": error.current},
            ) from None
        except NotFoundError:
            raise project_not_found() from None
        return _detail(stored)

    # ------------------------------------------------------------------ dry run

    async def dry_run(self, principal: Principal, body: s.DryRunIn) -> s.DryRunOut:
        self._throttle(principal, "dry_run")
        parsed = parse_policy(body.document)
        if parsed.policy is None:
            raise policy_invalid(parsed.issues)
        limits = self._limits
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(
                    text(f"SET LOCAL statement_timeout = {DRY_RUN_STATEMENT_TIMEOUT_MS}")
                )
                scope = await self._scope(conn, principal, body.project_id)
                assert scope is not None
                run_ids = await RunRepository(conn, principal.tenant).run_ids(
                    project_id=scope, since=None, limit=limits.max_runs + 1
                )
                runs_capped = len(run_ids) > limits.max_runs
                run_ids = run_ids[: limits.max_runs]
                events = await EventQueries(conn, principal.tenant).scan(
                    run_ids, MAPPED_EVENT_TYPES, limit=limits.max_events + 1
                )
                await conn.rollback()
        except DBAPIError as exc:
            if "statement timeout" in str(exc.orig) or "canceling statement" in str(exc.orig):
                raise AppError(
                    "POLICY_DRY_RUN_TIMEOUT",
                    "The dry run took too long; it was stopped.",
                    category=ErrorCategory.TIMEOUT,
                    status_code=504,
                    retryable=True,
                ) from exc
            raise
        events_capped = len(events) > limits.max_events
        result = await asyncio.to_thread(
            evaluate_events, parsed.policy, events[: limits.max_events], limits
        )
        result.runs_considered = len(run_ids)
        if result.truncated is None:
            result.truncated = "events" if events_capped else ("runs" if runs_capped else None)
        return _dry_run_out(result, limits)


def _dry_run_out(result: DryRunResult, limits: DryRunLimits) -> s.DryRunOut:
    return s.DryRunOut(
        runs_considered=result.runs_considered,
        events_evaluated=result.events_evaluated,
        events_skipped=result.events_skipped,
        truncated=result.truncated,  # type: ignore[arg-type]
        by_decision={k.value: result.by_decision.get(k.value, 0) for k in DecisionKind},
        by_rule=[
            s.DryRunRuleCount(rule_id=rule_id, decision=decision, count=count)  # type: ignore[arg-type]
            for (rule_id, decision), count in sorted(
                result.by_rule.items(), key=lambda kv: (-kv[1], kv[0][0] or "", kv[0][1])
            )
        ],
        samples=[
            s.DryRunSample(
                run_id=from_uuid(IdKind.RUN, hit.run_id),
                event_id=from_uuid(IdKind.EVENT, hit.event_id),
                capability=hit.capability,
                risk_class=hit.risk_class,
                decision=hit.decision.value,
                rule_id=hit.rule_id,
            )
            for hit in result.samples
        ],
        limits=s.DryRunLimitsOut(
            max_runs=limits.max_runs,
            max_events=limits.max_events,
            time_budget_seconds=limits.time_budget_seconds,
            max_samples=limits.max_samples,
        ),
    )
