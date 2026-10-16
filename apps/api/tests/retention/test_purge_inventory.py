"""Fail-closed: every table must be classified for a purge, and the deletion lists are the
classification (ADR-163)."""

import subprocess
import sys

from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import UUID

from abb_api.db.tables import metadata
from abb_api.retention import inventory as run_inventory
from abb_api.retention import purge_inventory as inv

# Handled by the run procedure of the retention engine (ADR-160), run by run.
RUN_ENGINE = set(run_inventory.CLASSIFICATION) | {"experiment_runs"}


def test_every_table_of_the_schema_is_classified_for_a_purge() -> None:
    missing = inv.unclassified(metadata)
    assert not missing, (
        f"tables with no purge classification: {sorted(missing)}. Add them to "
        "abb_api/retention/purge_inventory.py as purged-with-project, purged-with-workspace, "
        "kept-with-reason or exempt (with a reason) and, if purged, to the step that deletes them."
    )


def test_the_classification_names_only_tables_that_exist_and_every_entry_has_a_reason() -> None:
    assert set(inv.CLASSIFICATION) <= set(metadata.tables)
    assert all(entry.note.strip() for entry in inv.CLASSIFICATION.values())


def test_a_table_with_a_project_or_workspace_column_is_never_exempt() -> None:
    for name in inv.tenant_tables(metadata):
        assert inv.CLASSIFICATION[name].kind is not inv.Kind.EXEMPT, name
    exempt = set(inv.tables_of(inv.Kind.EXEMPT))
    assert not exempt & inv.tenant_tables(metadata)


def test_kept_tables_carry_no_predicate_and_purged_tables_always_do() -> None:
    for name, entry in inv.CLASSIFICATION.items():
        if entry.kind in (inv.Kind.KEPT, inv.Kind.EXEMPT):
            assert entry.project_where is None and entry.workspace_where is None, name
        elif entry.kind is inv.Kind.PROJECT:
            assert entry.project_where and entry.workspace_where, name
        else:
            assert entry.project_where is None and entry.workspace_where, name


def test_every_purged_table_has_exactly_one_way_out() -> None:
    """A purged table is emptied by the run procedure, a named drain, or a cascade (never none)."""
    drained = {t for t, _ in inv.PROJECT_DRAINS}
    cascaded = set(inv.PROJECT_CASCADED)
    for name in inv.tables_of(inv.Kind.PROJECT):
        ways = [name in RUN_ENGINE, name in drained, name in cascaded, name == "projects"]
        assert sum(ways) >= 1, f"{name} would survive a project purge"
    workspace_ways = (
        {t for t, _ in inv.WORKSPACE_DRAINS}
        | RUN_ENGINE
        | drained
        | cascaded
        | {"projects", "workspaces"}
        | {"retention_policies", "retention_reports", "retention_tombstones", "analytics_dirty"}
    )
    for name in (*inv.tables_of(inv.Kind.PROJECT), *inv.tables_of(inv.Kind.WORKSPACE)):
        assert name in workspace_ways, f"{name} would survive a workspace purge"


def test_the_drains_respect_the_foreign_keys() -> None:
    """A table is deleted before the tables it is referenced by (NO ACTION keys would refuse)."""
    order = [t for t, _ in inv.PROJECT_DRAINS]
    assert order.index("alert_rules") > order.index("alert_notifications")
    assert order.index("agents") > order.index("agent_versions")
    workspace = [t for t, _ in inv.WORKSPACE_DRAINS]
    assert workspace.index("alert_destinations") > workspace.index("alert_rules")
    assert workspace.index("alert_destinations") > workspace.index("alert_notifications")
    assert workspace.index("audit_log") < workspace.index("workspace_members")


def test_a_new_table_would_fail_the_check() -> None:
    fake = MetaData()
    Table("projects", fake, Column("id", UUID, primary_key=True))
    Table("zz_new", fake, Column("workspace_id", UUID), Column("x", Text))
    Table("zz_new_project", fake, Column("project_id", UUID))
    assert inv.unclassified(fake) == {"zz_new", "zz_new_project"}
    assert inv.tenant_tables(fake) >= {"zz_new", "zz_new_project"}


def test_the_api_process_never_imports_the_purge() -> None:
    """A compromised API process cannot purge: it does not even load the code (ADR-163)."""
    code = (
        "import sys\n"
        "from abb_api.core.config import Settings\n"
        "from abb_api.main import create_app\n"
        "app = create_app(Settings(database_url='postgresql+asyncpg://x@localhost/db'))\n"
        "bad = [m for m in sys.modules if m.startswith('abb_api.retention.purge') "
        "or m == 'abb_api.retention.runner']\n"
        "print(bad)\n"
        "raise SystemExit(1 if bad else 0)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
