"""What readers see of a run whose trace was removed (ADR-160): an explicit `trace_state`, an
intact record, and empty - never partial - events, spans and findings."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.retention.tick import run_tick
from tests.api_fixtures import Api
from tests.retention.enforce_helpers import enforcing, make_store
from tests.test_runs_api import ingest_scenario


async def test_a_run_past_the_trace_tier_says_expired_and_keeps_its_record(
    api: Api, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    run, events = await ingest_scenario(api)
    run_id = run["run_id"]
    before = (await api.get(f"/v1/runs/{run_id}")).json()
    assert before["trace_state"] == "available"
    assert len((await api.get(f"/v1/runs/{run_id}/events", limit=500)).json()["items"]) == len(
        events
    )

    # ~4 months later: past the trace tier (90 days), inside the run-record tier (365).
    result = await run_tick(
        retention_engine,
        enforcing(),
        budget_seconds=60,
        keep_reports=50,
        now=lambda: datetime(2027, 2, 10, tzinfo=UTC),
        store=make_store(tmp_path),
    )
    assert result.failed == 0 and result.expired_traces == 1 and result.deleted_runs == 0

    after = (await api.get(f"/v1/runs/{run_id}")).json()
    assert after["trace_state"] == "expired"
    # The record is intact: same summary, status and timings. Only the state differs, and the
    # findings badges, which are derived from detail that is gone, read "not analysed".
    derived = ("trace_state", "findings", "security_findings")
    assert {k: v for k, v in after.items() if k not in derived} == {
        k: v for k, v in before.items() if k not in derived
    }
    assert after["findings"]["state"] == after["security_findings"]["state"] == "none"
    assert (await api.get(f"/v1/runs/{run_id}/events", limit=500)).json()["items"] == []
    assert (await api.get(f"/v1/runs/{run_id}/spans")).json()["items"] == []
    listed = (await api.get("/v1/runs")).json()["items"]
    assert [r["trace_state"] for r in listed if r["id"] == run_id] == ["expired"]

    # Past the run-record tier the run is simply gone (404), with a tombstone left behind.
    await run_tick(
        retention_engine,
        enforcing(),
        budget_seconds=60,
        keep_reports=50,
        now=lambda: datetime(2028, 6, 1, tzinfo=UTC),
        store=make_store(tmp_path),
    )
    assert (await api.get(f"/v1/runs/{run_id}")).status_code == 404
