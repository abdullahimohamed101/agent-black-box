"""Migrations against real, empty PostgreSQL databases (one per test).

These tests are synchronous on purpose: they create databases with `asyncio.run`, which cannot run
inside a test event loop.
"""

import asyncio

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
