"""`Api.drain()` must not hide a job that failed and is waiting behind a retry backoff (KI-172)."""

import pytest
from sqlalchemy import text

from tests.api_fixtures import Api


async def test_drain_names_the_error_of_a_job_left_waiting_for_a_retry(api: Api) -> None:
    async with api.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO outbox_jobs (id, job_type, workspace_id, dedupe_key, status,"
                " attempt_count, available_at, last_error) VALUES (gen_random_uuid(),"
                " 'summarize_run', :ws, 'run-x', 'pending', 1, now() + interval '1 hour',"
                " 'OperationalError: connection reset')"
            ),
            {"ws": api.tenant.context.workspace_id},
        )
    with pytest.raises(AssertionError, match="summarize_run attempt 1: OperationalError"):
        await api.drain()
    await api.drain(expect_retries=True)  # a test about retries says so


async def test_drain_is_quiet_when_nothing_failed(api: Api) -> None:
    await api.drain()
