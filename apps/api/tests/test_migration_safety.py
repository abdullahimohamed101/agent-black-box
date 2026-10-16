"""The migration safety table (ADR-162): every migration is classified `reversible` or
`forward-only` in migrations/safety.json, and the classification is enforced.

A new migration must be added to that file in the same commit (see its `_how_to_classify` and
docs/runbooks/migrations.md). The chain test runs the whole history once, in-process: it upgrades
one revision at a time recording a fingerprint of the schema and its privileges, then downgrades
one at a time, and asserts that every `reversible` downgrade restores exactly the fingerprint of
the previous revision.
"""

import argparse
import ast
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import API_DIR, temporary_database
from tests.test_migrations import revisions

SAFETY = API_DIR / "migrations" / "safety.json"
VERSIONS = API_DIR / "migrations" / "versions"
CLASSES = {"reversible", "forward-only"}
HOW_TO = (
    "classify the new migration in apps/api/migrations/safety.json "
    "(see its _how_to_classify and docs/runbooks/migrations.md)"
)


def table() -> dict[str, dict[str, Any]]:
    return json.loads(SAFETY.read_text())["migrations"]  # type: ignore[no-any-return]


def files() -> dict[str, Path]:
    return {m.group(1): p for p in VERSIONS.glob("*.py") if (m := re.match(r"(\d{4})_", p.name))}


def test_every_migration_is_classified_and_nothing_stale_is() -> None:
    classified, on_disk = set(table()), set(files())
    assert on_disk == set(revisions())
    assert not on_disk - classified, (
        f"unclassified migration(s) {sorted(on_disk - classified)}: {HOW_TO}"
    )
    assert not classified - on_disk, (
        f"classified but no longer present: {sorted(classified - on_disk)}"
    )


def test_every_entry_is_well_formed() -> None:
    for revision, entry in table().items():
        assert entry["class"] in CLASSES, revision
        assert entry["notes"].strip(), revision
        if entry["class"] == "forward-only":
            assert "restore" in entry["notes"].lower(), (
                f"{revision}: say that rollback is a restore"
            )
        extra = set(entry) - {"class", "notes", "downgrade_discards", "upgrade_risk"}
        assert not extra, (revision, extra)


def test_a_reversible_migration_has_a_real_downgrade() -> None:
    for revision, entry in table().items():
        if entry["class"] != "reversible" or revision == "0001":
            continue
        tree = ast.parse(files()[revision].read_text())
        (downgrade,) = [
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade"
        ]
        body = [
            n
            for n in downgrade.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        ]
        assert body and not any(isinstance(n, ast.Pass | ast.Raise) for n in body), revision


def config(url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    return cfg


_FINGERPRINT = (
    "SELECT 'column ' || table_name || '.' || column_name || ' ' || data_type || ' ' || udt_name "
    "|| ' ' || coalesce(character_maximum_length::text, '') || ' ' "
    "|| coalesce(numeric_precision::text, '') || ',' || coalesce(numeric_scale::text, '') || ' ' "
    "|| is_nullable || ' ' || coalesce(column_default, '') || ' ' || is_identity "
    "FROM information_schema.columns WHERE table_schema = 'public' "
    "UNION ALL SELECT 'constraint ' || conrelid::regclass::text || ' ' || conname || ' ' "
    "|| pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
    "UNION ALL SELECT 'index ' || indexdef FROM pg_indexes WHERE schemaname = 'public' "
    "UNION ALL SELECT 'trigger ' || pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal "
    "UNION ALL SELECT 'function ' || proname || ' ' || md5(prosrc) FROM pg_proc "
    "WHERE pronamespace = 'public'::regnamespace "
    "UNION ALL SELECT 'acl ' || c.relname || ' ' || coalesce(c.relacl::text, '') FROM pg_class c "
    "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'S') "
    "UNION ALL SELECT 'colacl ' || c.relname || '.' || a.attname || ' ' || a.attacl::text "
    "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
    "WHERE c.relnamespace = 'public'::regnamespace AND a.attacl IS NOT NULL "
    "UNION ALL SELECT 'defacl ' || defaclobjtype::text || ' ' || defaclacl::text FROM pg_default_acl"
)


# An ACL that only gives the owner its own default rights is the same as no ACL (NULL).
_OWNER_ONLY = re.compile(r"^(acl \S+ )\{(\w+)=[a-zA-Z]+/\2\}$")


async def _fingerprint(url: str) -> frozenset[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return frozenset(
                _OWNER_ONLY.sub(r"\1", r[0]) for r in await conn.execute(text(_FINGERPRINT))
            )
    finally:
        await engine.dispose()


def fingerprint(url: str) -> frozenset[str]:
    return asyncio.run(_fingerprint(url))


def digest(lines: frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()[:12]


def test_every_reversible_downgrade_restores_the_previous_schema_and_privileges() -> None:
    """The whole chain once. Forward-only steps are run too (the database must stay walkable) but
    only reversible ones are held to the fingerprint of the revision below them."""
    chain = revisions()
    with temporary_database(migrate=False) as url:
        cfg = config(url)
        seen: dict[str, frozenset[str]] = {"base": fingerprint(url)}
        previous = "base"
        for revision in chain:
            command.upgrade(cfg, revision)
            seen[revision] = fingerprint(url)
            assert seen[revision] != seen[previous], (
                f"{revision} changed nothing"
            )  # a no-op 0001 aside
            previous = revision
        for index in range(len(chain) - 1, -1, -1):
            revision, below = chain[index], chain[index - 1] if index else "base"
            command.downgrade(cfg, below if index else "base")
            if table()[revision]["class"] != "reversible":
                continue
            after = fingerprint(url)
            if revision == "0001":
                continue  # the baseline creates nothing
            assert after == seen[below], (
                f"downgrade of {revision} did not restore {below}: "
                f"missing {sorted(seen[below] - after)[:3]}, "
                f"extra {sorted(after - seen[below])[:3]}"
            )
