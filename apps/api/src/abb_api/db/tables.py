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
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID

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
FINDING_KINDS = ("deterministic", "probable")
FINDING_SEVERITIES = ("info", "warning", "medium", "high", "critical")

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
    Column("device_hint", Text),
    UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    CheckConstraint("octet_length(token_hash) = 32", name="ck_sessions_token_hash"),
    CheckConstraint(
        "device_hint IS NULL OR char_length(device_hint) <= 64", name="ck_sessions_device_hint"
    ),
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
    Index(
        "ix_invitations_open_email",
        "email",
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
    CheckConstraint(
        "actor_kind IN ('user', 'api_key', 'cli', 'system')", name="ck_audit_actor_kind"
    ),
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
    # The throttle's periodic read of usable key ids (ADR-153): live keys only.
    Index("ix_api_keys_active", "key_id", postgresql_where=text("revoked_at IS NULL")),
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

# ------------------------------------------------ evaluations (Phase 11, ADR-090)

EVALUATOR_KINDS = ("deterministic", "metric", "human", "llm_judge")
EVALUATION_STATES = ("pending", "running", "completed", "failed")
EXPERIMENT_ROLES = ("baseline", "candidate")

# Workspace-level definitions of built-in evaluators (D2). Archived, never deleted: `evaluations`
# rows keep a snapshot and a RESTRICT foreign key. The byte limits are backstops: the pydantic
# models enforce them first (octet_length, not pg_column_size, which measures compressed size).
evaluators = Table(
    "evaluators",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True)),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("implementation", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("config", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("description", Text),
    Column("created_by", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("created_at", nullable=False, default_now=True),
    _ts("archived_at"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    UniqueConstraint("workspace_id", "name", name="uq_evaluators_name"),
    CheckConstraint(_in("kind", EVALUATOR_KINDS), name="ck_evaluators_kind"),
    CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_evaluators_config_object"),
    CheckConstraint("octet_length(config::text) <= 16384", name="ck_evaluators_config_size"),
    CheckConstraint(
        "char_length(name) <= 64 AND char_length(implementation) <= 64 "
        "AND char_length(description) <= 500",
        name="ck_evaluators_text_sizes",
    ),
)

# One row per application of an evaluator to a run (a re-run is a new row). A terminal row is
# never updated: `UPDATE ... WHERE state = 'pending'` is the only way out of pending, and a
# trigger (migration 0070) refuses any update of a completed or failed row (INV-1).
evaluations = Table(
    "evaluations",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    # Snapshots (the evaluator's slug and version at the time), so a row survives a rename.
    Column("evaluator_name", Text, nullable=False),
    Column("evaluator_version", Text, nullable=False),
    Column("score", Float),
    Column("label", Text),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at", nullable=False, default_now=True),
    Column("evaluator_id", UUID(as_uuid=True)),
    Column("kind", Text, nullable=False, server_default="deterministic"),
    Column("implementation", Text),
    Column("state", Text, nullable=False, server_default="pending"),
    Column("explanation", Text),
    Column("metrics", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("evidence_event_ids", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("config_snapshot", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error_code", Text),
    Column("error", Text),
    Column("requested_by", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("started_at"),
    _ts("completed_at"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    ForeignKeyConstraint(
        ["workspace_id", "evaluator_id"],
        ["evaluators.workspace_id", "evaluators.id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(_in("kind", EVALUATOR_KINDS), name="ck_evaluations_kind"),
    CheckConstraint(_in("state", EVALUATION_STATES), name="ck_evaluations_state"),
    CheckConstraint("score BETWEEN 0 AND 1", name="ck_evaluations_score"),
    CheckConstraint("octet_length(explanation) <= 16384", name="ck_evaluations_explanation_size"),
    CheckConstraint("octet_length(metrics::text) <= 16384", name="ck_evaluations_metrics_size"),
    CheckConstraint("octet_length(metadata::text) <= 16384", name="ck_evaluations_metadata_size"),
    CheckConstraint(
        "octet_length(config_snapshot::text) <= 16384", name="ck_evaluations_config_size"
    ),
    CheckConstraint(
        "jsonb_typeof(evidence_event_ids) = 'array' "
        "AND octet_length(evidence_event_ids::text) <= 8192",
        name="ck_evaluations_evidence",
    ),
    CheckConstraint(
        "char_length(label) <= 64 AND char_length(error_code) <= 64 AND char_length(error) <= 512 "
        "AND char_length(evaluator_name) <= 64 AND char_length(evaluator_version) <= 32",
        name="ck_evaluations_text_sizes",
    ),
    Index(
        "ix_evaluations_run_evaluator",
        "workspace_id",
        "run_id",
        "evaluator_id",
        text("created_at DESC"),
    ),
    Index(
        "ix_evaluations_open",
        "workspace_id",
        "state",
        postgresql_where=text("state IN ('pending', 'running')"),
    ),
)

# ------------------------------------------------ experiments (Phase 11, ADR-090)

experiments = Table(
    "experiments",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("dataset_label", Text),
    Column("evaluator_ids", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_by", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("created_at", nullable=False, default_now=True),
    _ts("archived_at"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]),
    CheckConstraint(
        "char_length(name) <= 128 AND char_length(description) <= 2000 "
        "AND char_length(dataset_label) <= 128",
        name="ck_experiments_text_sizes",
    ),
    CheckConstraint(
        "jsonb_typeof(evaluator_ids) = 'array' AND octet_length(evaluator_ids::text) <= 2048",
        name="ck_experiments_evaluator_ids",
    ),
    Index("ix_experiments_project", "workspace_id", "project_id", text("created_at DESC")),
)

experiment_variants = Table(
    "experiment_variants",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("experiment_id", UUID(as_uuid=True), nullable=False),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("config", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "experiment_id"],
        ["experiments.workspace_id", "experiments.id"],
        ondelete="CASCADE",
    ),
    UniqueConstraint("workspace_id", "experiment_id", "name", name="uq_experiment_variants_name"),
    CheckConstraint(_in("role", EXPERIMENT_ROLES), name="ck_experiment_variants_role"),
    CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_experiment_variants_config_object"),
    CheckConstraint(
        "octet_length(config::text) <= 8192", name="ck_experiment_variants_config_size"
    ),
    CheckConstraint("char_length(name) <= 64", name="ck_experiment_variants_name_size"),
    # Exactly one baseline per experiment (at most one here; the service requires one first).
    Index(
        "uq_experiment_variants_baseline",
        "workspace_id",
        "experiment_id",
        unique=True,
        postgresql_where=text("role = 'baseline'"),
    ),
)

experiment_runs = Table(
    "experiment_runs",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("experiment_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("variant_id", UUID(as_uuid=True), nullable=False),
    Column("added_by", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("added_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "experiment_id", "run_id"),
    ForeignKeyConstraint(
        ["workspace_id", "experiment_id"],
        ["experiments.workspace_id", "experiments.id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "variant_id"],
        ["experiment_variants.workspace_id", "experiment_variants.id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    Index("ix_experiment_runs_variant", "workspace_id", "variant_id"),
    Index("ix_experiment_runs_run", "workspace_id", "run_id"),
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

# ---------------------------------------------------------------- findings (Phase 10, ADR-080)

# Derived from a run's events by the `detect_findings` job (INV-2): rewritten wholesale per run,
# upserted by fingerprint. The id is derived from the fingerprint (UUIDv5), so it survives every
# rewrite. Evidence is metadata only; the CHECKs are a backstop for the model's own bounds.
findings = Table(
    "findings",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    # Copied from the run so a later project-level listing or rollup needs no join (ADR-043).
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("agent_slug", Text),
    _ts("run_started_at", nullable=False),
    Column("detector", Text, nullable=False),
    Column("detector_version", Text, nullable=False),
    Column("finding_type", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("start_event_id", UUID(as_uuid=True)),
    Column("end_event_id", UUID(as_uuid=True)),
    # Every event id the finding cites (<= 50), validated against the run's events.
    Column("evidence_event_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("evidence", JSONB, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    UniqueConstraint("workspace_id", "run_id", "fingerprint", name="uq_findings_run_fingerprint"),
    CheckConstraint(_in("kind", FINDING_KINDS), name="ck_findings_kind"),
    CheckConstraint(_in("severity", FINDING_SEVERITIES), name="ck_findings_severity"),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_findings_confidence"),
    CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_findings_evidence_object"),
    CheckConstraint("pg_column_size(evidence) < 16384", name="ck_findings_evidence_size"),
    CheckConstraint("cardinality(evidence_event_ids) <= 50", name="ck_findings_evidence_ids"),
    CheckConstraint(
        "char_length(detector) <= 64 AND char_length(detector_version) <= 32 "
        "AND char_length(finding_type) <= 64 AND char_length(fingerprint) = 64",
        name="ck_findings_text_sizes",
    ),
    Index("ix_findings_run", "workspace_id", "run_id"),
    Index("ix_findings_project_started", "workspace_id", "project_id", "run_started_at"),
    # The workspace Security view lists only security findings, newest run first (ADR-131).
    Index(
        "ix_findings_security",
        "workspace_id",
        "run_started_at",
        "id",
        postgresql_where=text("detector LIKE 'sec.%'"),
    ),
)

# One row per analysed run: what the analysis looked at, so findings can be marked outdated or
# truncated and a mismatch between the run row and the events is visible.
run_findings = Table(
    "run_findings",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("detectors_version", Integer, nullable=False),
    Column("summary_version_seen", Integer, nullable=False),
    Column("ordering_mode_seen", Text, nullable=False),
    _ts("computed_at", nullable=False),
    Column("event_count_seen", Integer, nullable=False),
    Column("truncated", Boolean, nullable=False),
    # Reliability findings only: the run list's badge predates the security category (ADR-131).
    Column("finding_count", Integer, nullable=False),
    Column("max_severity", Text),
    Column("security_count", Integer, nullable=False, server_default=text("0")),
    Column("security_max_severity", Text),
    PrimaryKeyConstraint("workspace_id", "run_id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    CheckConstraint(_in("ordering_mode_seen", ORDERING_MODES), name="ck_run_findings_ordering"),
    CheckConstraint(
        "max_severity IS NULL OR " + _in("max_severity", FINDING_SEVERITIES),
        name="ck_run_findings_severity",
    ),
    CheckConstraint(
        "security_max_severity IS NULL OR " + _in("security_max_severity", FINDING_SEVERITIES),
        name="ck_run_findings_security_severity",
    ),
    CheckConstraint("security_count >= 0", name="ck_run_findings_security_count"),
)

# ---------------------------------------------------------------- search (ADR-100, ADR-101)

_EMPTY_TEXT_ARRAY = text("'{}'::text[]")

# Derived per-run search document, written by the summarizer (INV-2). Metadata and content are
# separate columns with their own truncation flags so a statement for an actor without
# `payload.read` can be proven never to touch the content columns.
run_search = Table(
    "run_search",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    _ts("started_at", nullable=False),
    Column("summary_version", Integer, nullable=False),
    Column("document_hash", LargeBinary, nullable=False),
    Column("error_count", Integer, nullable=False, server_default="0"),
    Column("security_events", Integer, nullable=False, server_default="0"),
    Column("event_types", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("tools", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("models", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("providers", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("agents", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("tags", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("metadata_doc", TSVECTOR, nullable=False, server_default=text("''::tsvector")),
    Column("metadata_truncated", Boolean, nullable=False, server_default=text("false")),
    Column("file_paths", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("commands", PG_ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY),
    Column("content_doc", TSVECTOR, nullable=False, server_default=text("''::tsvector")),
    Column("content_truncated", Boolean, nullable=False, server_default=text("false")),
    _ts("updated_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "run_id"),
    ForeignKeyConstraint(
        ["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], ondelete="CASCADE"
    ),
    Index(
        "ix_run_search_project_started",
        "workspace_id",
        "project_id",
        text("started_at DESC"),
        "run_id",
    ),
    Index("ix_run_search_meta", "metadata_doc", postgresql_using="gin"),
    Index("ix_run_search_content", "content_doc", postgresql_using="gin"),
    Index("ix_run_search_event_types", "event_types", postgresql_using="gin"),
    Index("ix_run_search_tools", "tools", postgresql_using="gin"),
)

# ---------------------------------------------------------------- security settings (ADR-130)

CAPTURE_MODES = ("FULL", "METADATA_ONLY", "DISABLED")

# Per-project security configuration: how much the server stores (capture mode), which detectors a
# workspace owner muted and which destinations are expected (used by the Phase 13 detectors). A
# project without a row uses the defaults (METADATA_ONLY, no mutes, no allowlist), so the safe
# behaviour needs no backfill. Plain DML for the runtime role: this is configuration, not history;
# every change is recorded in `audit_log`.
project_security_settings = Table(
    "project_security_settings",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("capture_mode", Text, nullable=False, server_default="METADATA_ONLY"),
    Column("muted_detectors", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("destination_allowlist", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    _ts("updated_at", nullable=False, default_now=True),
    Column("updated_by", Text, nullable=False),
    PrimaryKeyConstraint("workspace_id", "project_id"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="CASCADE",
    ),
    CheckConstraint(_in("capture_mode", CAPTURE_MODES), name="ck_security_capture_mode"),
    CheckConstraint(
        "jsonb_typeof(muted_detectors) = 'array' AND jsonb_array_length(muted_detectors) <= 64",
        name="ck_security_muted_detectors",
    ),
    CheckConstraint(
        "jsonb_typeof(destination_allowlist) = 'array' "
        "AND jsonb_array_length(destination_allowlist) <= 256",
        name="ck_security_destination_allowlist",
    ),
    CheckConstraint("char_length(updated_by) <= 128", name="ck_security_updated_by"),
)

# ---------------------------------------------------------------- policies (ADR-140)

# Append-only history of policy documents (migration 0092 revokes UPDATE/DELETE from the runtime
# role). `project_id IS NULL` is the workspace-wide base policy. The current policy of a scope is
# its highest version. The document text is stored as published; nothing derived is stored.
policy_versions = Table(
    "policy_versions",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True)),
    Column("version", Integer, nullable=False),
    Column("document_text", Text, nullable=False),
    Column("document_sha256", Text, nullable=False),
    Column("rule_count", Integer, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    Column("created_by", Text, nullable=False),
    Column("comment", Text),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint("version >= 1", name="ck_policy_versions_version"),
    CheckConstraint(
        "octet_length(document_text) <= 65536", name="ck_policy_versions_document_size"
    ),
    CheckConstraint(
        "document_sha256 ~ '^[0-9a-f]{64}$'", name="ck_policy_versions_document_sha256"
    ),
    CheckConstraint("rule_count BETWEEN 0 AND 200", name="ck_policy_versions_rule_count"),
    CheckConstraint("char_length(created_by) <= 128", name="ck_policy_versions_created_by"),
    CheckConstraint(
        "comment IS NULL OR char_length(comment) <= 500", name="ck_policy_versions_comment"
    ),
    Index(
        "uq_policy_versions_workspace",
        "workspace_id",
        "version",
        unique=True,
        postgresql_where=text("project_id IS NULL"),
    ),
    Index(
        "uq_policy_versions_project",
        "workspace_id",
        "project_id",
        "version",
        unique=True,
        postgresql_where=text("project_id IS NOT NULL"),
    ),
)


# Approvals (Phase 14 step 2, ADR-141, migration 0093). The requested intent is immutable and the
# state machine is enforced by CHECKs and a BEFORE UPDATE trigger (`approvals_guard`, in the
# migration); the runtime role cannot DELETE or TRUNCATE. `*_scrubbed` columns hold only text that
# passed the Phase 13 scanner; `intent_hash` is SHA-256 of the unscrubbed canonical intent and is
# never returned or logged.
APPROVAL_STATES = ("REQUESTED", "APPROVED", "DENIED", "EXPIRED", "CANCELLED")
_OPEN = "state = 'REQUESTED' OR (state = 'APPROVED' AND consumed_at IS NULL)"
approvals = Table(
    "approvals",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("agent_id", Text, nullable=False),
    Column("capability", Text, nullable=False),
    Column("resource_scrubbed", Text),
    Column("risk_class", Text),
    Column("arguments_scrubbed", JSONB(none_as_null=True)),
    Column("scrub_dropped", Boolean, nullable=False, server_default=text("false")),
    Column("scrub_count", Integer, nullable=False, server_default=text("0")),
    Column("intent_hash", Text, nullable=False),
    Column("policy_rule_id", Text, nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("approver_role", Text),
    Column("allow_self_approval", Boolean, nullable=False),
    Column("on_expiry", Text, nullable=False),
    _ts("requested_at", nullable=False),
    _ts("expires_at", nullable=False),
    Column("requested_by_key_id", Text, nullable=False),
    Column("requested_by_user_id", UUID(as_uuid=True), ForeignKey("users.id")),
    Column("state", Text, nullable=False),
    Column("decided_by_user_id", UUID(as_uuid=True), ForeignKey("users.id")),
    _ts("decided_at"),
    _ts("closed_at"),
    Column("decision_comment", Text),
    _ts("consumed_at"),
    Column("consumed_run_id", UUID(as_uuid=True)),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(_in("state", APPROVAL_STATES), name="ck_approvals_state"),
    CheckConstraint(
        "(state = 'REQUESTED' AND decided_at IS NULL AND decided_by_user_id IS NULL "
        "AND closed_at IS NULL AND consumed_at IS NULL) "
        "OR (state IN ('APPROVED', 'DENIED') AND decided_at IS NOT NULL "
        "AND decided_by_user_id IS NOT NULL AND closed_at IS NULL) "
        "OR (state = 'CANCELLED' AND closed_at IS NOT NULL AND decided_at IS NULL "
        "AND decided_by_user_id IS NULL AND consumed_at IS NULL) "
        "OR (state = 'EXPIRED' AND closed_at IS NOT NULL "
        "AND (decided_at IS NULL) = (decided_by_user_id IS NULL) AND consumed_at IS NULL)",
        name="ck_approvals_shape",
    ),
    CheckConstraint(
        "(consumed_at IS NULL) = (consumed_run_id IS NULL) "
        "AND (consumed_at IS NULL OR state = 'APPROVED')",
        name="ck_approvals_consumed",
    ),
    CheckConstraint("intent_hash ~ '^[0-9a-f]{64}$'", name="ck_approvals_intent_hash"),
    CheckConstraint("expires_at > requested_at", name="ck_approvals_expiry"),
    CheckConstraint(
        "approver_role IS NULL OR approver_role IN ('OWNER', 'ADMIN', 'DEVELOPER', 'SECURITY')",
        name="ck_approvals_approver_role",
    ),
    CheckConstraint("on_expiry IN ('deny', 'allow')", name="ck_approvals_on_expiry"),
    CheckConstraint(
        "risk_class IS NULL OR risk_class IN ('R0', 'R1', 'R2', 'R3', 'R4')",
        name="ck_approvals_risk_class",
    ),
    CheckConstraint(
        "char_length(capability) <= 128 AND char_length(agent_id) <= 64 "
        "AND char_length(policy_rule_id) <= 64 AND char_length(requested_by_key_id) <= 128 "
        "AND (resource_scrubbed IS NULL OR char_length(resource_scrubbed) <= 4096) "
        "AND (decision_comment IS NULL OR char_length(decision_comment) <= 1000)",
        name="ck_approvals_text_sizes",
    ),
    CheckConstraint(
        "arguments_scrubbed IS NULL OR (jsonb_typeof(arguments_scrubbed) = 'object' "
        "AND pg_column_size(arguments_scrubbed) < 65536)",
        name="ck_approvals_arguments",
    ),
    CheckConstraint("scrub_count >= 0 AND policy_version >= 0", name="ck_approvals_counts"),
    Index(
        "ix_approvals_pending",
        "workspace_id",
        "requested_at",
        "id",
        postgresql_where=text("state = 'REQUESTED'"),
    ),
    Index("ix_approvals_listing", "workspace_id", text("requested_at DESC"), text("id DESC")),
    Index("ix_approvals_expiry", "workspace_id", "expires_at", postgresql_where=text(_OPEN)),
    Index(
        "uq_approvals_open_intent",
        "workspace_id",
        "run_id",
        "requested_by_key_id",
        "intent_hash",
        unique=True,
        postgresql_where=text(_OPEN),
    ),
    Index("ix_approvals_run", "workspace_id", "run_id"),
)


# Install-level secrets (migration 0094; append-only for the runtime role). The newest row of a
# name is current. Used for the key that derives server-written event ids (ADR-141 amendment 2).
server_secrets = Table(
    "server_secrets",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("secret", LargeBinary, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    CheckConstraint("octet_length(secret) >= 32", name="ck_server_secrets_length"),
    CheckConstraint("name ~ '^[a-z][a-z0-9-]{0,63}$'", name="ck_server_secrets_name"),
    Index("ix_server_secrets_name", "name", "id"),
)


# ---------------------------------------------------------------- alerting (Phase 17, ADR-150)

ALERT_TRIGGERS = (
    "approval_requested",
    "approval_expiring",
    "security_finding",
    "run_failed",
    "spend_over",
)
ALERT_DESTINATION_TYPES = ("webhook", "slack", "email")
ALERT_DESTINATION_STATES = ("active", "paused", "failing")
ALERT_NOTIFICATION_STATUSES = ("pending", "delivered", "failed", "dead", "skipped")
ALERT_SKIP_REASONS = (
    "cooldown",
    "destination_paused",
    "destination_deleted",
    "rule_deleted",
    "rate_limited",
    "daily_cap",
)

# Where an alert goes. The target (URL or recipients) and the signing secret exist only as AES-GCM
# ciphertext (`alerts/secrets.py`); `key_id` is the `server_secrets` row whose key sealed them.
# `masked_target` is a safe display form computed once, when the target is saved.
alert_destinations = Table(
    "alert_destinations",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("name", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("config_encrypted", LargeBinary, nullable=False),
    Column("signing_secret_encrypted", LargeBinary),
    Column("key_id", BigInteger, ForeignKey("server_secrets.id"), nullable=False),
    Column("masked_target", Text, nullable=False),
    Column("state", Text, nullable=False, server_default="active"),
    Column("consecutive_failures", Integer, nullable=False, server_default=text("0")),
    Column("paused_reason", Text),
    Column("created_by", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    Column("updated_by", Text, nullable=False),
    _ts("updated_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    UniqueConstraint("workspace_id", "name", name="uq_alert_destinations_name"),
    CheckConstraint(_in("type", ALERT_DESTINATION_TYPES), name="ck_alert_destinations_type"),
    CheckConstraint(_in("state", ALERT_DESTINATION_STATES), name="ck_alert_destinations_state"),
    CheckConstraint(
        "(type = 'webhook') = (signing_secret_encrypted IS NOT NULL)",
        name="ck_alert_destinations_signing",
    ),
    CheckConstraint(
        "octet_length(config_encrypted) BETWEEN 29 AND 8192",
        name="ck_alert_destinations_config_size",
    ),
    CheckConstraint("consecutive_failures >= 0", name="ck_alert_destinations_failures"),
    CheckConstraint(
        "char_length(name) BETWEEN 1 AND 80 AND char_length(masked_target) <= 400 "
        "AND char_length(created_by) <= 128 AND char_length(updated_by) <= 128",
        name="ck_alert_destinations_text_sizes",
    ),
    CheckConstraint(
        "paused_reason IS NULL OR paused_reason IN ('manual', 'failures')",
        name="ck_alert_destinations_paused_reason",
    ),
)

# What to watch and where to send it. `project_id IS NULL` means every project of the workspace.
# `filters` holds only the closed set of per-trigger fields the API validates.
alert_rules = Table(
    "alert_rules",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True)),
    Column("name", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("filters", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("cooldown_seconds", Integer, nullable=False, server_default=text("0")),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    _ts("last_fired_at"),
    Column("created_by", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    Column("updated_by", Text, nullable=False),
    _ts("updated_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("workspace_id", "name", name="uq_alert_rules_name"),
    CheckConstraint(_in("trigger", ALERT_TRIGGERS), name="ck_alert_rules_trigger"),
    CheckConstraint("jsonb_typeof(filters) = 'object'", name="ck_alert_rules_filters_object"),
    CheckConstraint("pg_column_size(filters) < 2048", name="ck_alert_rules_filters_size"),
    CheckConstraint("cooldown_seconds BETWEEN 0 AND 604800", name="ck_alert_rules_cooldown"),
    CheckConstraint(
        "char_length(name) BETWEEN 1 AND 80 AND char_length(created_by) <= 128 "
        "AND char_length(updated_by) <= 128",
        name="ck_alert_rules_text_sizes",
    ),
    Index("ix_alert_rules_trigger", "workspace_id", "trigger", postgresql_where=text("enabled")),
)

alert_rule_destinations = Table(
    "alert_rule_destinations",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("rule_id", UUID(as_uuid=True), nullable=False),
    Column("destination_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("workspace_id", "rule_id", "destination_id"),
    ForeignKeyConstraint(
        ["workspace_id", "rule_id"],
        ["alert_rules.workspace_id", "alert_rules.id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "destination_id"],
        ["alert_destinations.workspace_id", "alert_destinations.id"],
        ondelete="CASCADE",
    ),
    Index("ix_alert_rule_destinations_destination", "workspace_id", "destination_id"),
)

# One row per (rule, destination, event). `id` is the `alert_id` receivers dedupe on. The payload
# holds only the closed, content-free fields built in `alerts/payload.py`. A deleted rule or
# destination leaves its history with the reference cleared (nothing else about the row changes).
alert_notifications = Table(
    "alert_notifications",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False),
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("rule_id", UUID(as_uuid=True)),
    Column("destination_id", UUID(as_uuid=True)),
    Column("trigger", Text, nullable=False),
    Column("dedupe_key", Text, nullable=False),
    Column("project_id", UUID(as_uuid=True)),
    Column("run_id", UUID(as_uuid=True)),
    Column("finding_id", UUID(as_uuid=True)),
    Column("approval_id", UUID(as_uuid=True)),
    Column("agent_slug", Text),
    Column("severity", Text, nullable=False),
    Column("payload_json", JSONB, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("skip_reason", Text),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    _ts("next_attempt_at", nullable=False, default_now=True),
    Column("last_error_code", Text),
    _ts("created_at", nullable=False, default_now=True),
    _ts("delivered_at"),
    PrimaryKeyConstraint("workspace_id", "id"),
    ForeignKeyConstraint(
        ["workspace_id", "rule_id"],
        ["alert_rules.workspace_id", "alert_rules.id"],
        ondelete="SET NULL (rule_id)",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "destination_id"],
        ["alert_destinations.workspace_id", "alert_destinations.id"],
        ondelete="SET NULL (destination_id)",
    ),
    UniqueConstraint(
        "workspace_id", "rule_id", "destination_id", "dedupe_key", name="uq_alert_notifications_key"
    ),
    CheckConstraint(_in("trigger", ALERT_TRIGGERS), name="ck_alert_notifications_trigger"),
    CheckConstraint(
        _in("status", ALERT_NOTIFICATION_STATUSES), name="ck_alert_notifications_status"
    ),
    CheckConstraint(_in("severity", FINDING_SEVERITIES), name="ck_alert_notifications_severity"),
    CheckConstraint(
        "jsonb_typeof(payload_json) = 'object' AND pg_column_size(payload_json) < 4096",
        name="ck_alert_notifications_payload",
    ),
    CheckConstraint(
        "(status = 'skipped') = (skip_reason IS NOT NULL)", name="ck_alert_notifications_skip"
    ),
    CheckConstraint(
        "skip_reason IS NULL OR " + _in("skip_reason", ALERT_SKIP_REASONS),
        name="ck_alert_notifications_skip_reason",
    ),
    CheckConstraint(
        "(status = 'delivered') = (delivered_at IS NOT NULL)",
        name="ck_alert_notifications_delivered",
    ),
    CheckConstraint("attempts >= 0", name="ck_alert_notifications_attempts"),
    CheckConstraint(
        "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{0,47}$'",
        name="ck_alert_notifications_error_code",
    ),
    CheckConstraint(
        "char_length(dedupe_key) <= 200 AND (agent_slug IS NULL OR char_length(agent_slug) <= 200)",
        name="ck_alert_notifications_text_sizes",
    ),
    # The delivery worker (step 2) claims due rows across workspaces.
    Index(
        "ix_alert_notifications_due",
        "next_attempt_at",
        postgresql_where=text("status = 'pending'"),
    ),
    Index(
        "ix_alert_notifications_history", "workspace_id", text("created_at DESC"), text("id DESC")
    ),
    # The destination's rate limit and daily cap count what it was sent lately (ADR-151).
    Index(
        "ix_alert_notifications_delivered",
        "workspace_id",
        "destination_id",
        "delivered_at",
        postgresql_where=text("status = 'delivered'"),
    ),
    # Cooldown: "was a notification made for this rule and destination recently?"
    Index(
        "ix_alert_notifications_cooldown",
        "workspace_id",
        "rule_id",
        "destination_id",
        text("created_at DESC"),
    ),
)


# ------------------------------------------------------------ shared limiter (Phase 19a, ADR-152)

# Fixed-window counters shared by every API process (migration 0097). Short-lived operational
# state, not tenant data: no foreign key to `workspaces` (nothing here may delay deleting one),
# global scopes live under the nil workspace id. Rows are deleted once `expires_at` has passed.
rate_limit_counters = Table(
    "rate_limit_counters",
    metadata,
    Column("scope", Text, nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("key", Text, nullable=False),
    _ts("window_start", nullable=False),
    Column("count", BigInteger, nullable=False),
    _ts("expires_at", nullable=False),
    PrimaryKeyConstraint("scope", "workspace_id", "key", "window_start"),
    CheckConstraint("scope ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_rate_limit_counters_scope"),
    CheckConstraint("char_length(key) BETWEEN 1 AND 256", name="ck_rate_limit_counters_key"),
    CheckConstraint("count >= 0", name="ck_rate_limit_counters_count"),
    Index("ix_rate_limit_counters_expires", "expires_at"),
)


# ------------------------------------------------------------ project quotas (Phase 19a, ADR-154)

# Per-project overrides of the environment's quota defaults (migration 0098). A NULL column means
# "use the default"; a row exists only for a project an owner or admin has changed.
project_quota_overrides = Table(
    "project_quota_overrides",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("max_agents", Integer),
    Column("max_runs_per_day", Integer),
    Column("max_events_per_day", BigInteger),
    Column("max_artifact_bytes", BigInteger),
    _ts("updated_at", nullable=False, default_now=True),
    Column("updated_by", Text, nullable=False),
    PrimaryKeyConstraint("workspace_id", "project_id"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="CASCADE",
    ),
    CheckConstraint("max_agents BETWEEN 1 AND 1000000", name="ck_quota_overrides_agents"),
    CheckConstraint("max_runs_per_day BETWEEN 1 AND 100000000", name="ck_quota_overrides_runs"),
    CheckConstraint(
        "max_events_per_day BETWEEN 1 AND 100000000000", name="ck_quota_overrides_events"
    ),
    CheckConstraint(
        "max_artifact_bytes BETWEEN 1 AND 1125899906842624", name="ck_quota_overrides_artifacts"
    ),
    CheckConstraint("char_length(updated_by) <= 128", name="ck_quota_overrides_updated_by"),
)

# What a project has used on one UTC day, maintained in the transaction that stores the data
# (so it is exactly what committed, never more). One row per active project per day.
project_usage_daily = Table(
    "project_usage_daily",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("day", Date, nullable=False),
    Column("runs_created", BigInteger, nullable=False, server_default="0"),
    Column("events_accepted", BigInteger, nullable=False, server_default="0"),
    PrimaryKeyConstraint("workspace_id", "project_id", "day"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="CASCADE",
    ),
    CheckConstraint("runs_created >= 0 AND events_accepted >= 0", name="ck_usage_daily_counts"),
)

# Total stored artifact bytes of a project, kept in the transaction that records the artifact.
project_artifact_usage = Table(
    "project_artifact_usage",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("bytes_stored", BigInteger, nullable=False, server_default="0"),
    PrimaryKeyConstraint("workspace_id", "project_id"),
    ForeignKeyConstraint(
        ["workspace_id", "project_id"],
        ["projects.workspace_id", "projects.id"],
        ondelete="CASCADE",
    ),
    CheckConstraint("bytes_stored >= 0", name="ck_artifact_usage_bytes"),
)


# ------------------------------------------------------------ data lifecycle (Phase 19b, ADR-159)

RETENTION_POLICY_TIERS = ("artifacts", "trace", "run", "audit")
# Tiers a retention report can describe: the four the workspace sets, the fixed-window clean-up of
# a workspace's operational rows, and the install-level clean-up (`workspace_id` NULL).
RETENTION_REPORT_TIERS = (*RETENTION_POLICY_TIERS, "operational", "platform")
RETENTION_MODES = ("dry_run", "enforce")
RETENTION_FLOOR_DAYS = 7
RETENTION_CEILING_DAYS = 36500

# A workspace's retention policy (migration 0101). A tier's `*_days` NULL means "the environment
# default"; `*_forever` is the explicit choice to keep that tier for good (then `*_days` is NULL).
# The runtime role reads, inserts and updates it; the retention job only reads it.
retention_policies = Table(
    "retention_policies",
    metadata,
    Column(
        "workspace_id",
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    *[
        column
        for tier in RETENTION_POLICY_TIERS
        for column in (
            Column(f"{tier}_days", Integer),
            Column(f"{tier}_forever", Boolean, nullable=False, server_default=text("false")),
        )
    ],
    _ts("updated_at", nullable=False, default_now=True),
    Column("updated_by", Text, nullable=False),
    *[
        constraint
        for tier in RETENTION_POLICY_TIERS
        for constraint in (
            CheckConstraint(
                f"{tier}_days IS NULL OR {tier}_days BETWEEN {RETENTION_FLOOR_DAYS} "
                f"AND {RETENTION_CEILING_DAYS}",
                name=f"ck_retention_policies_{tier}_days",
            ),
            CheckConstraint(
                f"NOT {tier}_forever OR {tier}_days IS NULL",
                name=f"ck_retention_policies_{tier}_forever",
            ),
        )
    ],
    CheckConstraint("char_length(updated_by) <= 128", name="ck_retention_policies_updated_by"),
)

# What one retention tick found (dry run) or did (enforce) for one workspace and tier: counts only,
# never content. Written by the retention role; the application only reads. `workspace_id` is NULL
# exactly for the install-level `platform` tier.
retention_reports = Table(
    "retention_reports",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("tick_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")),
    Column("tier", Text, nullable=False),
    Column("mode", Text, nullable=False),
    Column("runs", BigInteger, nullable=False, server_default="0"),
    Column("events", BigInteger, nullable=False, server_default="0"),
    Column("artifact_bytes", BigInteger, nullable=False, server_default="0"),
    Column("row_count", BigInteger, nullable=False, server_default="0"),
    Column("truncated", Boolean, nullable=False, server_default=text("false")),
    _ts("cutoff_at"),
    _ts("started_at", nullable=False),
    _ts("finished_at", nullable=False),
    CheckConstraint(_in("tier", RETENTION_REPORT_TIERS), name="ck_retention_reports_tier"),
    CheckConstraint(_in("mode", RETENTION_MODES), name="ck_retention_reports_mode"),
    CheckConstraint(
        "(workspace_id IS NULL) = (tier = 'platform')", name="ck_retention_reports_scope"
    ),
    CheckConstraint(
        "runs >= 0 AND events >= 0 AND artifact_bytes >= 0 AND row_count >= 0",
        name="ck_retention_reports_counts",
    ),
    Index("ix_retention_reports_workspace", "workspace_id", "tier", text("started_at DESC")),
)

# One row per run the retention job removed data of (step 2; unused in step 1): the run id, the
# tier and when. Written by the retention role; the application only reads.
retention_tombstones = Table(
    "retention_tombstones",
    metadata,
    Column(
        "workspace_id",
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("tier", Text, nullable=False),
    Column("mode", Text, nullable=False),
    _ts("deleted_at", nullable=False, default_now=True),
    PrimaryKeyConstraint("workspace_id", "run_id", "tier"),
    CheckConstraint(
        _in("tier", ("artifacts", "trace", "run")), name="ck_retention_tombstones_tier"
    ),
    CheckConstraint(_in("mode", RETENTION_MODES), name="ck_retention_tombstones_mode"),
)
