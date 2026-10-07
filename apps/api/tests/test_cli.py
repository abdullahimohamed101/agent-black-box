import io
import os
import stat
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api import cli
from abb_api.auth.service import authenticate
from abb_api.core.config import Settings
from abb_api.core.errors import AppError
from abb_api.projects.repository import ProjectRepository
from abb_api.tenancy import TenantContext
from abb_api.workspaces import WorkspaceProvisioning
from tests.conftest import make_settings
from tests.test_auth_service import Tick


async def invoke(settings: Settings, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await cli.run(list(argv), settings, clock=Tick(), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


async def test_create_workspace_project_and_key_end_to_end(
    engine: AsyncEngine, database_url: str
) -> None:
    settings = make_settings(database_url)
    assert (await invoke(settings, "create-workspace", "--name", "Acme", "--slug", "acme"))[0] == 0
    code, _, _ = await invoke(
        settings, "create-project", "--workspace", "acme", "--name", "Agent", "--slug", "agent"
    )
    assert code == 0
    code, out, err = await invoke(
        settings, "create-key", "--workspace", "acme", "--project", "agent",
        "--scopes", "events:write", "runs:read", "--name", "ci",
    )  # fmt: skip
    assert code == 0
    token = out.strip()
    assert token.startswith("abb_live_") and token not in err  # secret on stdout only
    async with engine.begin() as conn:
        principal = await authenticate(conn, token, Tick())
        assert principal.scopes == {"events:write", "runs:read"}


async def test_errors_are_reported_without_a_traceback(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(database_url)
    await invoke(settings, "create-workspace", "--name", "Acme", "--slug", "acme")
    code, _, err = await invoke(settings, "create-workspace", "--name", "Dup", "--slug", "acme")
    assert code == 2 and "already exists" in err and "Traceback" not in err
    code, _, err = await invoke(
        settings, "create-project", "--workspace", "nope", "--name", "x", "--slug", "x"
    )
    assert code == 1 and "not found" in err
    code, _, err = await invoke(
        settings, "create-key", "--workspace", "acme", "--scopes", "admin:all"
    )
    assert code == 2 and "unknown scopes" in err


async def test_revoke_key(database_url: str, engine: AsyncEngine) -> None:
    settings = make_settings(database_url)
    await invoke(settings, "create-workspace", "--name", "Acme", "--slug", "acme")
    _, token, _ = await invoke(
        settings, "create-key", "--workspace", "acme", "--scopes", "runs:read"
    )
    key_id = token.strip().removeprefix("abb_live_").split(".")[0]
    code, _, err = await invoke(
        settings, "revoke-key", "--workspace", "acme", "--key-id", token.strip()
    )
    assert code == 1 and "key id" in err  # passing the secret token is refused, not logged
    assert (await invoke(settings, "revoke-key", "--workspace", "acme", "--key-id", key_id))[0] == 0
    async with engine.begin() as conn:
        with pytest.raises(AppError):
            await authenticate(conn, token.strip(), Tick())


async def test_seed_is_idempotent_and_writes_a_private_key_file(
    database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    settings = make_settings(database_url)
    key_file = tmp_path / "keys" / "dev-api-key"
    code, out, err = await invoke(settings, "seed", "--key-file", str(key_file))
    assert code == 0 and out == ""  # nothing secret on stdout
    token = key_file.read_text().strip()
    assert token not in err and token.startswith("abb_live_")
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    async with engine.begin() as conn:
        assert (await authenticate(conn, token, Tick())).project_id is not None

    code, _, err = await invoke(settings, "seed", "--key-file", str(key_file))
    assert code == 0 and "still valid" in err
    assert key_file.read_text().strip() == token  # unchanged

    key_file.write_text("abb_live_" + "a" * 12 + "." + "B" * 43 + "\n")  # stale/bad file
    await invoke(settings, "seed", "--key-file", str(key_file))
    fresh = key_file.read_text().strip()
    assert fresh != token
    async with engine.begin() as conn:
        await authenticate(conn, fresh, Tick())
        with pytest.raises(AppError):  # the previous seed key was revoked, not left active
            await authenticate(conn, token, Tick())


async def test_seed_refuses_production(database_url: str, tmp_path: Path) -> None:
    settings = Settings(environment="production", database_url=database_url)
    code, _, err = await invoke(settings, "seed", "--key-file", str(tmp_path / "k"))
    assert code == 1 and "production" in err and not (tmp_path / "k").exists()


async def test_seed_creates_the_expected_tenant(
    database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    await invoke(make_settings(database_url), "seed", "--key-file", str(tmp_path / "k"))
    async with engine.connect() as conn:
        workspace = await WorkspaceProvisioning(conn).get_by_slug("local")
        assert workspace is not None
        project = await ProjectRepository(conn, TenantContext(workspace.id)).get_by_slug("demo")
        assert project is not None


async def test_operators_can_list_and_requeue_dead_letters(
    database_url: str, engine: AsyncEngine
) -> None:
    from sqlalchemy import insert, select

    from abb_api.db import tables as t
    from tests.factories import make_workspace, uid

    settings = make_settings(database_url)
    async with engine.begin() as conn:
        ws = await make_workspace(conn)
        dead = uid()
        for job_id, status in ((dead, "dead_letter"), (uid(), "done")):
            await conn.execute(
                insert(t.outbox_jobs).values(
                    id=job_id, job_type="summarize_run", workspace_id=ws, dedupe_key=str(job_id),
                    status=status, attempt_count=5, last_error="ValueError: boom\nsecond line",
                )
            )  # fmt: skip
    code, out, _ = await invoke(settings, "jobs-list")
    assert code == 0 and str(dead) in out and "ValueError: boom second line" in out
    assert out.count("\n") == 1  # only the dead letter, one line each
    code, _, err = await invoke(settings, "jobs-retry", "--id", str(dead))
    assert code == 0 and "requeued 1" in err
    async with engine.connect() as conn:
        row = (await conn.execute(select(t.outbox_jobs).where(t.outbox_jobs.c.id == dead))).one()
    assert row.status == "pending" and row.attempt_count == 0
    assert (await invoke(settings, "jobs-list"))[1] == ""
