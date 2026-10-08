"""Table definitions (SQLAlchemy Core). The single description of the schema.

Conventions (plan D2, ADR-002):

* Every tenant table has `workspace_id` and its primary key is `(workspace_id, id)`: two tenants
  cannot collide on an id or observe each other's rows.
* Children reference `(workspace_id, parent_id)` with composite foreign keys, so a row cannot
  point at another tenant's parent even if application code is wrong.
* Ids are `uuid` columns holding the 128 bits of the public prefixed ULIDs (`ids.to_uuid`).
* Accepted events are append-only (INV-1); `runs` and `spans` are derived and rewritten only by
  the summarizer (INV-2).

Migrations are hand-reviewed snapshots; `tests/test_migrations.py` fails if they drift
from this file.
"""

from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> Column[Any]:
    return Column(
        name,
        DateTime(timezone=True),
        nullable=nullable,
        server_default=func.now() if default_now else None,
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


ROLES = ("OWNER", "ADMIN", "DEVELOPER", "VIEWER", "SECURITY", "BILLING")
API_KEY_SCOPES = ("events:write", "runs:read", "artifacts:write", "policy:check")
RUN_STATUSES = (
    "QUEUED",
    "RUNNING",
    "WAITING",
    "WAITING_FOR_APPROVAL",
    "SUCCESS",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "BLOCKED",
)
ORDERING_MODES = ("sequence", "time")
JOB_STATUSES = ("pending", "running", "done", "dead_letter")
POLICY_ACTIONS = ("allow", "deny", "require_approval")

# ---------------------------------------------------------------- tenancy and identity

workspaces = Table(
    "workspaces",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("slug", Text, nullable=False, unique=True),
    _ts("created_at", nullable=False, default_now=True),
)

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", Text, nullable=False),
    Column("name", Text),
    _ts("created_at", nullable=False, default_now=True),
    Index("uq_users_email_lower", func.lower(Column("email", Text)), unique=True),
)

workspace_members = Table(
    "workspace_members",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("role", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "user_id"),
    CheckConstraint(_in("role", ROLES), name="ck_workspace_members_role"),
)

projects = Table(
    "projects",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("name", Text, nullable=False),
    Column("slug", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
)

agents = Table(
    "agents",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("slug", Text, nullable=False),
    Column("name", Text),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    UniqueConstraint("workspace_id", "project_id", "slug", name="uq_agents_project_slug"),
)

agent_versions = Table(
    "agent_versions",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("fingerprint", Text, nullable=False),
    _ts("first_seen_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
    UniqueConstraint(
        "workspace_id", "agent_id", "fingerprint", name="uq_agent_versions_fingerprint"
    ),
)

# The one table looked up before the tenant is known (by `key_id`), hence its own surrogate key.
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("key_id", Text, nullable=False, unique=True),
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    # NULL = a workspace-wide key (read access across projects); ingestion requires a project.
    Column("project_id", UUID(as_uuid=True)),
    Column("secret_hash", LargeBinary, nullable=False),
    Column("scopes", ARRAY(Text), nullable=False),
    Column("name", Text),
    Column("created_by", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("created_at", nullable=False, default_now=True),
    _ts("last_used_at"),
    _ts("expires_at"),
    _ts("revoked_at"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    CheckConstraint(
        "scopes <@ ARRAY[" + ", ".join(f"'{s}'" for s in API_KEY_SCOPES) + "]::text[]",
        name="ck_api_keys_scopes",
    ),
)

# ---------------------------------------------------------------- runs, spans, events

runs = Table(
    "runs",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("agent_slug", Text),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("name", Text),
    Column("status", Text, nullable=False),
    Column("ordering_mode", Text, nullable=False, server_default="sequence"),
    _ts("started_at", nullable=False),
    _ts("completed_at"),
    Column("duration_ms", Float),
    Column("summary", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("summary_version", Integer, nullable=False, server_default="0"),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    CheckConstraint(_in("status", RUN_STATUSES), name="ck_runs_status"),
    CheckConstraint(_in("ordering_mode", ORDERING_MODES), name="ck_runs_ordering_mode"),
    Index("ix_runs_project_started", "workspace_id", "project_id", text("started_at DESC"), "id"),
    Index("ix_runs_status_started", "workspace_id", "status", text("started_at DESC"), "id"),
)

spans = Table(
    "spans",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("parent_span_id", UUID(as_uuid=True)),
    Column("name", Text),
    Column("kind", Text),
    Column("agent_slug", Text, nullable=False),
    Column("status", Text),
    _ts("started_at"),
    _ts("ended_at"),
    Column("duration_ms", Float),
    Column("event_count", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    Index("ix_spans_run_parent", "workspace_id", "run_id", "parent_span_id"),
)

events = Table(
    "events",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("span_id", UUID(as_uuid=True)),
    Column("parent_span_id", UUID(as_uuid=True)),
    Column("agent_id", Text, nullable=False),
    Column("agent_version", Text),
    Column("event_type", Text, nullable=False),
    _ts("occurred_at", nullable=False),
    _ts("received_at", nullable=False, default_now=True),
    Column("sequence", BigInteger),
    Column("status", Text),
    Column("duration_ms", Float),
    Column("attributes", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # none_as_null: an absent payload is SQL NULL, not the JSON value `null`
    Column("payload", JSONB(none_as_null=True)),
    Column("payload_ref", Text),
    Column("tags", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("schema_version", Text, nullable=False),
    Column("sdk", JSONB(none_as_null=True)),
    # SHA-256 of the canonical event (dedup.content_hash): separates a retry from a conflict.
    Column("content_hash", LargeBinary, nullable=False),
    PrimaryKeyConstraint("workspace_id", "event_id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    Index("ix_events_run_sequence", "workspace_id", "run_id", "sequence", "event_id"),
    Index("ix_events_run_time", "workspace_id", "run_id", "occurred_at", "event_id"),
    # Live streams follow a run in arrival order (ADR-022).
    Index("ix_events_run_arrival", "workspace_id", "run_id", "received_at", "event_id"),
    Index("ix_events_type_time", "workspace_id", "event_type", text("occurred_at DESC")),
)

# ---------------------------------------------------------------- background jobs

outbox_jobs = Table(
    "outbox_jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("job_type", Text, nullable=False),
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    # Coalescing key: many events of one run produce one pending job (partial unique index below).
    Column("dedupe_key", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    _ts("available_at", nullable=False, default_now=True),
    Column("lease_owner", Text),
    _ts("lease_expires_at"),
    Column("last_error", Text),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
    CheckConstraint(_in("status", JOB_STATUSES), name="ck_outbox_jobs_status"),
    Index(
        "uq_outbox_pending_dedupe",
        "job_type",
        "dedupe_key",
        unique=True,
        postgresql_where=text("status = 'pending'"),
    ),
    Index("ix_outbox_claim", "available_at", postgresql_where=text("status = 'pending'")),
    Index("ix_outbox_lease", "lease_expires_at", postgresql_where=text("status = 'running'")),
    # Run listings look up each run's job state by key; without this they scan the whole history.
    Index("ix_outbox_dedupe", "workspace_id", "job_type", "dedupe_key", "status"),
)

# ------------------------ skeletons (extended by later phases)

artifacts = Table(
    "artifacts",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    # No foreign key to runs (ADR-030): an artifact may arrive before the first event of its run.
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("span_id", UUID(as_uuid=True)),
    Column("artifact_type", Text, nullable=False),
    Column("name", Text),
    Column("media_type", Text, nullable=False, server_default="text/plain"),
    Column("storage_uri", Text, nullable=False),
    Column("size_bytes", BigInteger),
    Column("content_hash", LargeBinary),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    Index("ix_artifacts_run", "workspace_id", "run_id"),
)

evaluations = Table(
    "evaluations",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("evaluator_name", Text, nullable=False),
    Column("evaluator_version", Text, nullable=False),
    Column("score", Float),
    Column("label", Text),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
)

policies = Table(
    "policies",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True)),
    Column("name", Text, nullable=False),
    Column("rule", JSONB, nullable=False),
    Column("action", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("version", Integer, nullable=False, server_default="1"),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    CheckConstraint(_in("action", POLICY_ACTIONS), name="ck_policies_action"),
)
