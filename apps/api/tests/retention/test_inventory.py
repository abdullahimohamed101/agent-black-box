"""Fail-closed: a table that holds a run's data must be classified for retention (ADR-160)."""

from sqlalchemy import Column, ForeignKey, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db.tables import metadata
from abb_api.retention import inventory as inv
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import enforce, fingerprint, make_store
from tests.retention.helpers import days_ago, seed_run


def test_every_run_bound_table_is_classified() -> None:
    missing = inv.unclassified(metadata)
    assert not missing, (
        f"tables that name a run but have no retention classification: {sorted(missing)}. "
        "Add them to abb_api/retention/inventory.py as retained-detail, retained-record or "
        "exempt (with a reason) and, for detail, to the step that deletes them."
    )


def test_the_classification_names_only_tables_that_exist_and_only_run_bound_ones() -> None:
    assert set(inv.CLASSIFICATION) <= set(metadata.tables)
    # `runs` is the thing the others name, and outbox_jobs names a run only in its coalescing key.
    assert set(inv.CLASSIFICATION) - inv.run_bound_tables(metadata) == {"runs", "outbox_jobs"}
    assert all(entry.note for entry in inv.CLASSIFICATION.values())


def test_a_new_table_with_a_run_id_would_fail_the_check() -> None:
    fake = MetaData()
    Table("runs", fake, Column("id", UUID, primary_key=True))
    Table("zz_new_detail", fake, Column("run_id", UUID, nullable=False), Column("x", Text))
    Table("zz_consumer", fake, Column("consumed_run_id", UUID))
    Table("zz_fk", fake, Column("r", UUID, ForeignKey("runs.id")))
    Table("zz_unrelated", fake, Column("id", UUID))
    assert inv.unclassified(fake) == {"zz_new_detail", "zz_consumer", "zz_fk"}


def test_every_detail_table_has_exactly_one_deleting_step() -> None:
    steps = [*inv.DETAIL_BATCHED, *inv.SEARCH_TABLES, *inv.ARTIFACT_TABLES, "outbox_jobs"]
    assert sorted(steps) == sorted(inv.tables_of(inv.Kind.DETAIL))


async def test_enforcement_leaves_no_row_in_any_detail_table(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: object
) -> None:
    """The classification is the behaviour: every detail table of the inventory is emptied."""
    from pathlib import Path

    store = make_store(Path(str(tmp_path)))
    acme = await make_tenant(engine, "acme")
    run = await seed_run(
        engine, acme, ended=days_ago(100), store=store, full=True, artifact_sizes=(3,)
    )
    before = await fingerprint(engine, run.run_id)
    assert all(before[t][0] > 0 for t in inv.tables_of(inv.Kind.DETAIL))  # all populated
    await enforce(retention_engine, store)
    after = await fingerprint(engine, run.run_id)
    assert all(after[t][0] == 0 for t in inv.tables_of(inv.Kind.DETAIL))
    assert after["runs"][0] == 1  # the record stays until its own tier
