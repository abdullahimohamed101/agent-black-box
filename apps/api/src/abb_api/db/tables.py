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
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
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
COST_SOURCES = ("provider_reported", "estimated", "client_estimate", "unpriced")

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
    # OIDC identity (ADR-060). `provider` is the issuer URL. Both NULL = pre-provisioned, not yet
    # linked; the pair is unique, and may only be set while NULL (UserRepository.link_identity).
    Column("provider", Text),
    Column("provider_subject", Text),
    _ts("email_verified_at"),
    _ts("last_login_at"),
    Index("uq_users_email_lower", func.lower(Column("email", Text)), unique=True),
    UniqueConstraint("provider", "provider_subject", name="uq_users_identity"),
    CheckConstraint(
        "(provider IS NULL) = (provider_subject IS NULL)", name="ck_users_identity_pair"
    ),
)

# People's sessions and login attempts are user-level, not tenant-keyed (like api_keys.key_id they
# are found by an unguessable token, here its SHA-256, before any workspace is chosen).
sessions = Table(
    "sessions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("token_hash", LargeBinary, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    _ts("last_seen_at", nullable=False, default_now=True),
    _ts("expires_at", nullable=False),
    _ts("idle_expires_at", nullable=False),
    _ts("revoked_at"),
    UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    CheckConstraint("octet_length(token_hash) = 32", name="ck_sessions_token_hash"),
    Index("ix_sessions_user", "user_id"),
    Index("ix_sessions_expires", "expires_at"),
)

login_states = Table(
    "login_states",
    metadata,
    Column("state_hash", LargeBinary, primary_key=True),
    Column("nonce", Text, nullable=False),
    Column("code_verifier", Text, nullable=False),
    Column("return_to", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    _ts("expires_at", nullable=False),
    CheckConstraint("octet_length(state_hash) = 32", name="ck_login_states_state_hash"),
    Index("ix_login_states_expires", "expires_at"),
)

workspace_members = Table(
    "workspace_members",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("role", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at"),
    Column("invited_by", UUID(as_uuid=True), ForeignKey("users.id")),
    PrimaryKeyConstraint("workspace_id", "user_id"),
    CheckConstraint(_in("role", ROLES), name="ck_workspace_members_role"),
)

# One-time invitation links bound to an email (D12). Found by the SHA-256 of the token before the
# workspace is known, like sessions; everything else is tenant-keyed.
invitations = Table(
    "invitations",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("email", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("token_hash", LargeBinary, nullable=False),
    Column("invited_by", UUID(as_uuid=True), ForeignKey("users.id")),
    Column("accepted_by", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("created_at", nullable=False, default_now=True),
    _ts("expires_at", nullable=False),
    _ts("accepted_at"),
    _ts("revoked_at"),
    PrimaryKeyConstraint("workspace_id", "id"),
    UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    CheckConstraint("octet_length(token_hash) = 32", name="ck_invitations_token_hash"),
    CheckConstraint(_in("role", ROLES), name="ck_invitations_role"),
    CheckConstraint(
        "email = lower(email) AND char_length(email) BETWEEN 3 AND 254", name="ck_invitations_email"
    ),
    Index(
        "uq_invitations_open_email",
        "workspace_id",
        "email",
        unique=True,
        postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
    ),
)

# Administrative history (D11): the runtime role may only SELECT and INSERT (migration 0047).
audit_log = Table(
    "audit_log",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", BigInteger, Identity(always=True), nullable=False),
    Column("actor_kind", Text, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("resource_kind", Text),
    Column("resource_id", Text),
    Column("outcome", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("request_id", Text),
    _ts("occurred_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    CheckConstraint("actor_kind IN ('user', 'api_key', 'cli')", name="ck_audit_actor_kind"),
    CheckConstraint("outcome IN ('allowed', 'denied')", name="ck_audit_outcome"),
    CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_audit_details_object"),
    CheckConstraint("pg_column_size(details) < 8192", name="ck_audit_details_size"),
    CheckConstraint(
        "char_length(actor_id) <= 128 AND char_length(action) <= 64 "
        "AND char_length(resource_kind) <= 64 AND char_length(resource_id) <= 128 "
        "AND char_length(request_id) <= 64",
        name="ck_audit_text_sizes",
    ),
    Index("ix_audit_workspace_time", "workspace_id", "occurred_at", "id"),
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
    # Typed copies of summary figures, written with it by the summarizer (INV-2: rebuilt).
    # Analytics aggregate these instead of parsing JSONB for every row (measured, ADR-043).
    Column("cost_usd", Numeric(38, 9), nullable=False, server_default="0"),
    Column("retry_cost_usd", Numeric(38, 9), nullable=False, server_default="0"),
    Column("llm_calls", Integer, nullable=False, server_default="0"),
    Column("tool_calls", Integer, nullable=False, server_default="0"),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    Column("retries_unattributed", Integer, nullable=False, server_default="0"),
    Column("files_modified", Integer, nullable=False, server_default="0"),
    Column("unpriced_calls", Integer, nullable=False, server_default="0"),
    Column("tool_spans_finished", Integer, nullable=False, server_default="0"),
    Column("tool_spans_ok", Integer, nullable=False, server_default="0"),
    Column("llm_spans_finished", Integer, nullable=False, server_default="0"),
    Column("llm_spans_ok", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    CheckConstraint(_in("status", RUN_STATUSES), name="ck_runs_status"),
    CheckConstraint(_in("ordering_mode", ORDERING_MODES), name="ck_runs_ordering_mode"),
    Index("ix_runs_project_started", "workspace_id", "project_id", text("started_at DESC"), "id"),
    Index("ix_runs_status_started", "workspace_id", "status", text("started_at DESC"), "id"),
    # Workspace-wide windows and cross-project listings (KI-025; analytics windows, ADR-043).
    Index("ix_runs_workspace_started", "workspace_id", text("started_at DESC"), "id"),
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
    # Copied from the run by the summarizer so span analytics filter without a join (ADR-043).
    Column("project_id", UUID(as_uuid=True)),
    _ts("run_started_at"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    Index("ix_spans_run_parent", "workspace_id", "run_id", "parent_span_id"),
    Index("ix_spans_window", "workspace_id", "run_started_at", "kind"),
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

# ---------------------------------------------------------------- cost (Phase 7, ADR-040)

# One row per completed model call, derived from events by the summarizer (INV-2): rewritten
# wholesale for a run whenever the run is summarized. `run_started_at` is copied from the run so
# aggregates filter and bucket without a join; money is exact `numeric` (9 decimal places).
cost_calculations = Table(
    "cost_calculations",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("agent_slug", Text, nullable=False),
    Column("span_id", UUID(as_uuid=True)),
    _ts("occurred_at", nullable=False),
    _ts("run_started_at", nullable=False),
    Column("provider", Text),
    Column("model", Text),
    Column("input_tokens", BigInteger, nullable=False),
    Column("output_tokens", BigInteger, nullable=False),
    Column("cached_input_tokens", BigInteger, nullable=False),
    Column("source", Text, nullable=False),
    Column("pricing_version", Text),
    Column("pricing_origin", Text),
    Column("input_cost", Numeric(38, 9)),
    Column("output_cost", Numeric(38, 9)),
    Column("cached_cost", Numeric(38, 9)),
    Column("request_cost", Numeric(38, 9)),
    Column("estimated_total", Numeric(38, 9)),
    Column("reported_total", Numeric(38, 9)),
    Column("client_total", Numeric(38, 9)),
    Column("total", Numeric(38, 9), nullable=False),
    Column("is_retry", Boolean, nullable=False, server_default=text("false")),
    PrimaryKeyConstraint("workspace_id", "event_id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    CheckConstraint(_in("source", COST_SOURCES), name="ck_cost_calculations_source"),
    Index("ix_cost_run", "workspace_id", "run_id"),
    Index("ix_cost_project_started", "workspace_id", "project_id", "run_started_at"),
)

# User price overrides: append-only (a correction is a newer row), workspace- or project-scoped.
pricing_overrides = Table(
    "pricing_overrides",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True)),
    Column("provider", Text),
    Column("model_pattern", Text, nullable=False),
    Column("input_per_million", Numeric(38, 9), nullable=False),
    Column("output_per_million", Numeric(38, 9), nullable=False),
    Column("cached_input_per_million", Numeric(38, 9)),
    Column("request_price", Numeric(38, 9), nullable=False, server_default="0"),
    _ts("valid_from", nullable=False),
    Column("note", Text),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    CheckConstraint(
        "input_per_million >= 0 AND output_per_million >= 0 AND request_price >= 0 "
        "AND (cached_input_per_million IS NULL OR cached_input_per_million >= 0)",
        name="ck_pricing_overrides_nonnegative",
    ),
    Index("ix_pricing_overrides_workspace", "workspace_id", "project_id"),
)

# ---------------------------------------------------------------- analytics rollups (ADR-043)
#
# Daily aggregates of the derived tables, rebuilt per (workspace, UTC day) by the
# `refresh_analytics_day` job (delete + insert), so they are derived state like everything else here
# (INV-2). They exist because scanning 700,000 runs / 6,000,000 spans per dashboard request was
# measured at 1.5-9 s (docs/benchmarks/phase-7-analytics.md). Today's (still changing) day is never
# read from here: the read path aggregates it live.


def _daily(name: str, *columns: Column[Any], key: tuple[str, ...]) -> Table:
    return Table(
        name,
        metadata,
        Column("workspace_id", UUID(as_uuid=True), nullable=False),
        Column("project_id", UUID(as_uuid=True), nullable=False),
        Column("day", Date, nullable=False),
        *columns,
        PrimaryKeyConstraint("workspace_id", "project_id", "day", *key),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]
        ),
        Index(f"ix_{name}_day", "workspace_id", "day"),
    )


def _bigint(name: str) -> Column[Any]:
    return Column(name, BigInteger, nullable=False, server_default="0")


def _money(name: str) -> Column[Any]:
    return Column(name, Numeric(38, 9), nullable=False, server_default="0")


analytics_runs_daily = _daily(
    "analytics_runs_daily",
    Column("agent_slug", Text, nullable=False),
    Column("status", Text, nullable=False),
    _bigint("runs"),
    _money("cost_usd"),
    _money("retry_cost_usd"),
    _bigint("retried_runs"),
    _bigint("runs_with_retry_cost"),
    _bigint("llm_calls"),
    _bigint("tool_calls"),
    _bigint("retry_count"),
    _bigint("retries_unattributed"),
    _bigint("files_modified"),
    _bigint("unpriced_calls"),
    _bigint("tool_spans_finished"),
    _bigint("tool_spans_ok"),
    _bigint("llm_spans_finished"),
    _bigint("llm_spans_ok"),
    _bigint("unrebuilt_runs"),
    key=("agent_slug", "status"),
)

analytics_cost_daily = _daily(
    "analytics_cost_daily",
    Column("agent_slug", Text, nullable=False),
    Column("provider", Text, nullable=False),  # '' when unknown
    Column("model", Text, nullable=False),
    Column("source", Text, nullable=False),
    _bigint("calls"),
    _money("total_usd"),
    _bigint("input_tokens"),
    _bigint("output_tokens"),
    key=("agent_slug", "provider", "model", "source"),
)

analytics_spans_daily = _daily(
    "analytics_spans_daily",
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),  # '' for kinds that are not listed by name
    _bigint("finished"),
    _bigint("ok"),
    key=("kind", "name"),
)

# Duration histograms (log-spaced buckets, `analytics/percentiles.py`): percentiles are merged
# across days from these.
analytics_latency_daily = _daily(
    "analytics_latency_daily",
    Column("series", Text, nullable=False),  # 'run' or a span kind
    Column("name", Text, nullable=False),
    Column("bucket", SmallInteger, nullable=False),
    _bigint("n"),
    key=("series", "name", "bucket"),
)

analytics_top_runs = _daily(
    "analytics_top_runs",
    Column("kind", Text, nullable=False),  # 'cost' | 'retries'
    Column("rank", SmallInteger, nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    _money("cost_usd"),
    _money("retry_cost_usd"),
    _bigint("retry_count"),
    key=("kind", "rank"),
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
