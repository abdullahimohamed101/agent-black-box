"""Migrations against real, empty PostgreSQL databases (one per test).

These tests are synchronous on purpose: they create databases with `asyncio.run`, which cannot run
inside a test event loop.
"""

import asyncio
import uuid
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.db.tables import metadata
from tests.conftest import API_DIR, alembic, temporary_database


def revisions() -> list[str]:
    """Revision ids from the first migration to head."""
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    script = ScriptDirectory.from_config(config)
    return [r.revision for r in reversed(list(script.walk_revisions()))]


async def table_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


async def column_names(url: str, table: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


async def index_names(url: str, table: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t"),
            {"t": table},
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


def test_there_is_a_linear_history_with_the_expected_revisions() -> None:
    assert revisions() == [
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0040",
        "0041",
        "0042",
        "0043",
        "0044",
        "0045",
        "0046",
        "0047",
        "0060",
        "0080",
        "0070",
        "0071",
        "0090",
        "0091",
        "0092",
        "0093",
        "0094",
        "0095",
        "0096",
        "0097",
        "0098",
        "0099",
        "0100",
        "0101",
        "0102",
        "0103",
    ]


def test_empty_database_upgrades_to_head() -> None:
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "head")
        assert {t.name for t in metadata.sorted_tables} <= asyncio.run(table_names(url))


@pytest.mark.parametrize("revision", revisions())
def test_each_revision_upgrades_downgrades_and_upgrades_again(revision: str) -> None:
    """Every step is reversible and repeatable, starting from the previous revision."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", revision)
        alembic(url, "downgrade", "-1")
        alembic(url, "upgrade", revision)


def test_previous_head_upgrades_to_head_and_downgrade_to_base_is_clean() -> None:
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "head-1")
        alembic(url, "upgrade", "head")
        alembic(url, "downgrade", "base")
        assert asyncio.run(table_names(url)) == {"alembic_version"}


async def test_migrations_match_the_table_definitions(database_url: str) -> None:
    """tables.py and the hand-reviewed migrations describe the same schema (no drift)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        diffs = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), metadata)
        )
    await engine.dispose()
    assert diffs == []


def test_a_database_at_0044_upgrades_to_head_and_back_to_0044() -> None:
    """Phase 15's three migrations apply on top of the previous release and reverse cleanly."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0044")
        before = asyncio.run(table_names(url))
        alembic(url, "upgrade", "head")
        after = asyncio.run(table_names(url))
        assert {"sessions", "login_states", "invitations", "audit_log"} <= after - before
        alembic(url, "downgrade", "0044")
        assert asyncio.run(table_names(url)) == before


def test_a_database_at_0047_upgrades_to_0060_and_back_to_0047() -> None:
    """Phase 10's migration adds the findings tables and removes exactly them."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0047")
        before = asyncio.run(table_names(url))
        alembic(url, "upgrade", "0060")
        assert asyncio.run(table_names(url)) - before == {"findings", "run_findings"}
        alembic(url, "downgrade", "0047")
        assert asyncio.run(table_names(url)) == before


def test_0080_creates_the_search_table_and_drops_it_again() -> None:
    """Phase 16: an empty derived table with its five indexes; the parent keeps working."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0060")
        assert "run_search" not in asyncio.run(table_names(url))
        alembic(url, "upgrade", "0080")
        assert "run_search" in asyncio.run(table_names(url))
        assert asyncio.run(index_names(url, "run_search")) == {
            "run_search_pkey",
            "ix_run_search_project_started",
            "ix_run_search_meta",
            "ix_run_search_content",
            "ix_run_search_event_types",
            "ix_run_search_tools",
        }
        alembic(url, "downgrade", "0060")
        assert "run_search" not in asyncio.run(table_names(url))


def test_a_database_at_0080_upgrades_to_0071_and_back_to_0080() -> None:
    """Phase 11's two migrations add exactly their tables and columns and reverse cleanly."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0080")
        before = asyncio.run(table_names(url))
        columns_before = asyncio.run(column_names(url, "evaluations"))
        alembic(url, "upgrade", "0071")
        assert asyncio.run(table_names(url)) - before == {
            "evaluators",
            "experiments",
            "experiment_variants",
            "experiment_runs",
        }
        assert {"state", "evaluator_id", "explanation", "config_snapshot"} <= (
            asyncio.run(column_names(url, "evaluations")) - columns_before
        )
        alembic(url, "downgrade", "0080")
        assert asyncio.run(table_names(url)) == before
        assert asyncio.run(column_names(url, "evaluations")) == columns_before


def test_0090_creates_the_security_settings_table_and_drops_it_again() -> None:
    """Phase 13: one additive table; the previous head upgrades to it and back."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0071")
        before = asyncio.run(table_names(url))
        assert "project_security_settings" not in before
        alembic(url, "upgrade", "0090")
        assert asyncio.run(table_names(url)) - before == {"project_security_settings"}
        assert asyncio.run(column_names(url, "project_security_settings")) == {
            "workspace_id",
            "project_id",
            "capture_mode",
            "muted_detectors",
            "destination_allowlist",
            "updated_at",
            "updated_by",
        }
        alembic(url, "downgrade", "0071")
        assert asyncio.run(table_names(url)) == before


def test_0091_widens_severities_and_adds_the_security_badge_and_back() -> None:
    """Phase 13 step 2: medium/high severities, a separate security badge, a partial index."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0090")
        before = asyncio.run(column_names(url, "run_findings"))
        alembic(url, "upgrade", "0091")
        assert asyncio.run(column_names(url, "run_findings")) - before == {
            "security_count",
            "security_max_severity",
        }
        assert "ix_findings_security" in asyncio.run(index_names(url, "findings"))
        alembic(url, "downgrade", "0090")
        assert asyncio.run(column_names(url, "run_findings")) == before
        assert "ix_findings_security" not in asyncio.run(index_names(url, "findings"))


def test_0092_creates_the_policy_versions_table_and_drops_it_again() -> None:
    """Phase 14: one additive append-only table; the previous head upgrades to it and back, and a
    fresh database reaches the same schema."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0091")
        before = asyncio.run(table_names(url))
        assert "policy_versions" not in before
        alembic(url, "upgrade", "0092")
        assert asyncio.run(table_names(url)) - before == {"policy_versions"}
        assert asyncio.run(column_names(url, "policy_versions")) == {
            "workspace_id",
            "id",
            "project_id",
            "version",
            "document_text",
            "document_sha256",
            "rule_count",
            "created_at",
            "created_by",
            "comment",
        }
        assert {"uq_policy_versions_workspace", "uq_policy_versions_project"} <= asyncio.run(
            index_names(url, "policy_versions")
        )
        alembic(url, "downgrade", "0091")
        assert asyncio.run(table_names(url)) == before


def test_0093_creates_the_approvals_table_and_trigger_and_drops_them_again() -> None:
    """Phase 14 step 2: one additive table with its guard trigger; the previous head upgrades to
    it and back, and the audit log's actor kinds gain `system` and lose it again."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0092")
        before = asyncio.run(table_names(url))
        triggers_before = asyncio.run(trigger_names(url))
        assert "approvals" not in before
        alembic(url, "upgrade", "0093")
        assert asyncio.run(table_names(url)) - before == {"approvals"}
        columns = asyncio.run(column_names(url, "approvals"))
        assert {"intent_hash", "state", "consumed_at", "requested_by_key_id"} <= columns
        assert {"uq_approvals_open_intent", "ix_approvals_pending", "ix_approvals_expiry"} <= (
            asyncio.run(index_names(url, "approvals"))
        )
        assert asyncio.run(trigger_names(url)) - triggers_before == {
            "approvals_guard",
            "approvals_born",
        }
        alembic(url, "downgrade", "0092")
        assert asyncio.run(table_names(url)) == before
        assert asyncio.run(trigger_names(url)) == triggers_before


async def trigger_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


def test_0094_creates_the_append_only_server_secrets_table_and_drops_it_again() -> None:
    """Phase 14 review fix P1-1: one additive table; the previous head upgrades to it and back."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0093")
        before = asyncio.run(table_names(url))
        assert "server_secrets" not in before
        alembic(url, "upgrade", "0094")
        assert asyncio.run(table_names(url)) - before == {"server_secrets"}
        alembic(url, "downgrade", "0093")
        assert asyncio.run(table_names(url)) == before


def test_0095_creates_the_alerting_tables_and_drops_them_again() -> None:
    """Phase 17 step 1: four additive tables; the previous head upgrades to it and back."""
    created = {
        "alert_destinations",
        "alert_rules",
        "alert_rule_destinations",
        "alert_notifications",
    }
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0094")
        before = asyncio.run(table_names(url))
        assert not created & before
        alembic(url, "upgrade", "0095")
        assert asyncio.run(table_names(url)) - before == created
        assert {"ix_alert_notifications_due", "ix_alert_notifications_history"} <= asyncio.run(
            index_names(url, "alert_notifications")
        )
        alembic(url, "downgrade", "0094")
        assert asyncio.run(table_names(url)) == before


def test_0097_creates_the_rate_limit_counters_and_drops_them_again() -> None:
    """Phase 19a step 1: one additive table; the previous head upgrades to it and back."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0096")
        before = asyncio.run(table_names(url))
        assert "rate_limit_counters" not in before
        alembic(url, "upgrade", "0097")
        assert asyncio.run(table_names(url)) - before == {"rate_limit_counters"}
        assert {"ix_rate_limit_counters_expires"} <= asyncio.run(
            index_names(url, "rate_limit_counters")
        )
        alembic(url, "downgrade", "0096")
        assert asyncio.run(table_names(url)) == before


def test_0098_creates_the_quota_tables_backfills_artifact_bytes_and_drops_them_again() -> None:
    """Phase 19a step 3: three additive tables; existing artifacts count from the first request."""
    created = {"project_quota_overrides", "project_usage_daily", "project_artifact_usage"}
    workspace, alpha, beta = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def seed(url: str) -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO workspaces (id, name, slug) VALUES (:w, 'w', 'w')"),
                {"w": workspace},
            )
            for project, slug in ((alpha, "alpha"), (beta, "beta")):
                await conn.execute(
                    text(
                        "INSERT INTO projects (workspace_id, id, name, slug) "
                        "VALUES (:w, :p, :s, :s)"
                    ),
                    {"w": workspace, "p": project, "s": slug},
                )
            # alpha: 100 + 250 bytes and one artifact of unknown size; beta: none
            for size in (100, 250, None):
                await conn.execute(
                    text(
                        "INSERT INTO artifacts (workspace_id, id, run_id, project_id, "
                        "artifact_type, storage_uri, size_bytes) "
                        "VALUES (:w, :a, :r, :p, 'stdout', 'k', :size)"
                    ),
                    {
                        "w": workspace,
                        "a": uuid.uuid4(),
                        "r": uuid.uuid4(),
                        "p": alpha,
                        "size": size,
                    },
                )
        await engine.dispose()

    async def backfilled(url: str) -> dict[uuid.UUID, int]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT project_id, bytes_stored FROM project_artifact_usage")
            )
            found = {r[0]: int(r[1]) for r in rows}
        await engine.dispose()
        return found

    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0097")
        before = asyncio.run(table_names(url))
        assert not created & before
        asyncio.run(seed(url))
        alembic(url, "upgrade", "0098")
        assert asyncio.run(table_names(url)) - before == created
        assert asyncio.run(backfilled(url)) == {alpha: 350}  # beta has no artifacts: no row
        alembic(url, "downgrade", "0097")
        assert asyncio.run(table_names(url)) == before
        alembic(url, "upgrade", "head")  # and the data survives the round trip
        assert asyncio.run(backfilled(url)) == {alpha: 350}


def test_0099_adds_the_session_device_hint_column_and_invitation_index_and_drops_them() -> None:
    """Phase 19a step 5: one nullable column and one partial index; old sessions have no hint."""

    async def insert_session(url: str) -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id, email) VALUES (gen_random_uuid(), 'a@x.test')")
            )
            await conn.execute(
                text(
                    "INSERT INTO sessions (id, user_id, token_hash, expires_at, idle_expires_at) "
                    "SELECT gen_random_uuid(), id, decode(repeat('ab', 32), 'hex'), "
                    "now() + interval '1 day', now() + interval '1 day' FROM users"
                )
            )
        await engine.dispose()

    async def hints(url: str) -> list[object]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            found = (await conn.execute(text("SELECT device_hint FROM sessions"))).scalars().all()
        await engine.dispose()
        return list(found)

    async def too_long(url: str) -> bool:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE sessions SET device_hint = repeat('x', 65)"))
        except Exception:
            return True
        finally:
            await engine.dispose()
        return False

    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0098")
        assert "device_hint" not in asyncio.run(column_names(url, "sessions"))
        asyncio.run(insert_session(url))
        alembic(url, "upgrade", "0099")
        assert "device_hint" in asyncio.run(column_names(url, "sessions"))
        assert "ix_invitations_open_email" in asyncio.run(index_names(url, "invitations"))
        assert asyncio.run(hints(url)) == [None]
        assert asyncio.run(too_long(url))  # the length is bounded in the database too
        alembic(url, "downgrade", "0098")
        assert "device_hint" not in asyncio.run(column_names(url, "sessions"))
        assert "ix_invitations_open_email" not in asyncio.run(index_names(url, "invitations"))
        alembic(url, "upgrade", "head")


def test_0100_revokes_delete_on_the_quota_tables_and_adds_the_active_key_index() -> None:
    """Phase 19a review: reversible; the runtime role keeps SELECT, INSERT and UPDATE."""

    async def facts(url: str) -> dict[str, bool]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            deletes = {
                name: bool(
                    (
                        await conn.execute(
                            text("SELECT has_table_privilege('abb_runtime', :n, 'DELETE')"),
                            {"n": name},
                        )
                    ).scalar_one()
                )
                for name in (
                    "project_quota_overrides",
                    "project_usage_daily",
                    "project_artifact_usage",
                )
            }
        await engine.dispose()
        return deletes

    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0099")
        before = asyncio.run(facts(url))
        assert all(before.values())
        assert "ix_api_keys_active" not in asyncio.run(index_names(url, "api_keys"))
        alembic(url, "upgrade", "0100")
        after = asyncio.run(facts(url))
        assert not any(after.values())
        assert "ix_api_keys_active" in asyncio.run(index_names(url, "api_keys"))
        alembic(url, "downgrade", "0099")
        restored = asyncio.run(facts(url))
        assert all(restored.values())
        assert "ix_api_keys_active" not in asyncio.run(index_names(url, "api_keys"))
        alembic(url, "upgrade", "head")


def test_0101_adds_the_retention_tables_and_role_and_downgrades_cleanly() -> None:
    """Phase 19b: 0100 -> 0101 keeps data, creates the role with its grants; the downgrade removes
    the tables and every grant of the role (the role itself is cluster-wide and stays)."""

    async def facts(url: str) -> dict[str, Any]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            grants = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.role_table_grants "
                        "WHERE grantee = 'abb_retention'"
                    )
                )
            ).scalar_one()
            columns = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.column_privileges "
                        "WHERE grantee = 'abb_retention'"
                    )
                )
            ).scalar_one()
            role = (
                await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'abb_retention'"))
            ).first()
            workspaces = (await conn.execute(text("SELECT count(*) FROM workspaces"))).scalar_one()
        await engine.dispose()
        return {"grants": grants, "columns": columns, "role": role is not None, "ws": workspaces}

    retention_tables = {"retention_policies", "retention_reports", "retention_tombstones"}
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0100")
        engine = create_async_engine(url, poolclass=NullPool)

        async def seed() -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO workspaces (id, name, slug) "
                        "VALUES (gen_random_uuid(), 'a', 'a')"
                    )
                )
            await engine.dispose()

        asyncio.run(seed())
        before = asyncio.run(table_names(url))
        assert not retention_tables & before
        alembic(url, "upgrade", "0101")
        assert retention_tables <= asyncio.run(table_names(url))
        up = asyncio.run(facts(url))
        assert up["role"] is True and up["ws"] == 1
        assert up["grants"] > 0 and up["columns"] > 0
        assert asyncio.run(column_names(url, "retention_policies")) >= {
            "workspace_id",
            "artifacts_days",
            "artifacts_forever",
            "audit_days",
            "audit_forever",
            "updated_by",
        }
        alembic(url, "downgrade", "0100")
        down = asyncio.run(facts(url))
        assert asyncio.run(table_names(url)) == before
        assert (down["grants"], down["columns"], down["role"], down["ws"]) == (0, 0, True, 1)
        alembic(url, "upgrade", "0101")
        assert asyncio.run(facts(url)) == up


def test_0102_adds_the_enforcement_schema_and_downgrades_cleanly() -> None:
    """Phase 19b step 2: 0101 -> 0102 keeps runs (defaulting to `available`), creates the progress
    and dirty tables and the job's extra grants; the downgrade removes all of it."""

    async def facts(url: str) -> dict[str, Any]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as conn:
            grants = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.role_table_grants "
                        "WHERE grantee = 'abb_retention'"
                    )
                )
            ).scalar_one()
            columns = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.column_privileges "
                        "WHERE grantee = 'abb_retention'"
                    )
                )
            ).scalar_one()
        await engine.dispose()
        return {"grants": grants, "columns": columns}

    new_tables = {"retention_run_progress", "analytics_dirty"}
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "0101")
        before = asyncio.run(facts(url))
        names = asyncio.run(table_names(url))
        assert not new_tables & names
        assert "trace_state" not in asyncio.run(column_names(url, "runs"))

        async def seed() -> None:
            engine = create_async_engine(url, poolclass=NullPool)
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO workspaces (id, name, slug) "
                        "VALUES ('00000000-0000-0000-0000-0000000000a1', 'a', 'a')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO projects (workspace_id, id, name, slug) VALUES "
                        "('00000000-0000-0000-0000-0000000000a1', "
                        "'00000000-0000-0000-0000-0000000000b1', 'p', 'p')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO runs (workspace_id, id, project_id, trace_id, status, "
                        "started_at) VALUES ('00000000-0000-0000-0000-0000000000a1', "
                        "gen_random_uuid(), '00000000-0000-0000-0000-0000000000b1', "
                        "gen_random_uuid(), 'SUCCESS', now())"
                    )
                )
            await engine.dispose()

        asyncio.run(seed())
        alembic(url, "upgrade", "0102")
        assert new_tables <= asyncio.run(table_names(url))
        assert "trace_state" in asyncio.run(column_names(url, "runs"))
        up = asyncio.run(facts(url))
        assert up["grants"] > before["grants"] and up["columns"] > before["columns"]

        async def states() -> list[str]:
            engine = create_async_engine(url, poolclass=NullPool)
            async with engine.connect() as conn:
                rows = await conn.execute(text("SELECT trace_state FROM runs"))
                out = [r[0] for r in rows]
            await engine.dispose()
            return out

        assert asyncio.run(states()) == ["available"]
        alembic(url, "downgrade", "0101")
        assert asyncio.run(table_names(url)) == names
        assert asyncio.run(facts(url)) == before
        alembic(url, "upgrade", "0102")  # not head: 0103 adds a column the table-level grants cover
        assert asyncio.run(facts(url)) == up
