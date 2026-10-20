"""KI-097: while a newer analysis is queued, health reads the last completed one and says so."""

from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from abb_event_schema.ids import to_uuid
from sqlalchemy import text

from abb_api.findings.health import FindingRef, FindingsState, health
from abb_api.findings.model import Severity
from abb_api.jobs.outbox import OutboxRepository
from tests.api_fixtures import Api
from tests.test_findings_api import findings, fixture_wires, post_wires, run_id_of
from tests.test_findings_health import dims, facts, ref

AS_OF = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)
LOOP = ref("possible_loop", Severity.WARNING)


def with_as_of(state: FindingsState, as_of: datetime | None = AS_OF):  # type: ignore[no-untyped-def]
    return {d.name: d for d in health(facts(), [LOOP], state, as_of=as_of).dimensions}


def test_processing_with_a_stored_result_reads_it_and_is_stale() -> None:
    d = with_as_of("processing")
    assert d["loops"].status == "warn" and d["loops"].stale and d["loops"].as_of == AS_OF
    assert d["context"].status == "ok" and d["context"].stale and d["context"].as_of == AS_OF
    assert d["loops"].finding_ids == ("fnd_1",)
    assert "earlier analysis" in d["loops"].rule


def test_current_is_not_stale_and_carries_its_age() -> None:
    d = with_as_of("current")
    assert d["loops"].status == "warn" and not d["loops"].stale and d["loops"].as_of == AS_OF


def test_processing_without_a_stored_result_stays_no_data() -> None:
    d = with_as_of("processing", None)
    assert d["loops"].status == "no_data" and d["loops"].as_of is None and not d["loops"].stale
    assert "in progress" in d["loops"].rule


@pytest.mark.parametrize("state", [s for s in get_args(FindingsState) if s != "current"])
def test_only_processing_borrows_the_earlier_result(state: str) -> None:
    d = with_as_of(state)  # type: ignore[arg-type]
    if state == "processing":
        assert d["loops"].status == "warn"
    else:
        assert d["loops"].status == "no_data" and d["loops"].as_of is None


def test_dimensions_that_do_not_read_findings_carry_no_age() -> None:
    d = with_as_of("processing")
    assert d["reliability"].as_of is None and not d["reliability"].stale
    assert d["tool_reliability"].as_of is None


def test_the_cost_spike_part_of_cost_efficiency_uses_the_earlier_result_too() -> None:
    spike = FindingRef(id="fnd_s", finding_type="cost_spike", severity=Severity.WARNING)
    d = {x.name: x for x in health(facts(), [spike], "processing", as_of=AS_OF).dimensions}
    assert d["cost_efficiency"].status == "warn" and d["cost_efficiency"].stale
    assert d["cost_efficiency"].inputs["cost_spike"] is True


def test_existing_callers_without_as_of_are_unchanged() -> None:
    assert dims(facts(), [LOOP], "processing")["loops"].status == "no_data"


async def queue_a_newer_analysis(api: Api, run_id: str) -> None:
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_detect(
            [to_uuid(run_id)], delay=timedelta(hours=1)
        )


async def test_a_live_run_shows_the_last_analysis_labelled_stale(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    run_id = run_id_of("looping")
    current = await findings(api, run_id)
    assert current["state"]["status"] == "current"
    assert all(d["stale"] is False for d in current["health"]["dimensions"])

    await queue_a_newer_analysis(api, run_id)
    body = await findings(api, run_id)
    assert body["state"]["status"] == "processing"
    by_name = {d["name"]: d for d in body["health"]["dimensions"]}
    assert by_name["loops"]["status"] != "no_data"
    assert by_name["loops"]["stale"] is True
    assert by_name["loops"]["as_of"] == current["state"]["computed_at"]
    assert by_name["loops"]["finding_ids"]  # the earlier result's findings are cited
    assert body["server_time"] >= current["server_time"]
    assert by_name["reliability"]["stale"] is False and by_name["reliability"]["as_of"] is None


async def test_a_never_analysed_run_still_says_analysis_in_progress(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    assert await api.worker.run_once() >= 1  # summarised, analysis queued, never computed
    body = await findings(api, run_id_of("looping"))
    assert body["state"]["status"] == "processing" and body["state"]["computed_at"] is None
    loops = next(d for d in body["health"]["dimensions"] if d["name"] == "loops")
    assert loops["status"] == "no_data" and loops["as_of"] is None and loops["stale"] is False
    assert "in progress" in loops["rule"]


async def test_results_from_older_rules_are_not_borrowed_while_queued(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    run_id = run_id_of("looping")
    async with api.engine.begin() as conn:
        await conn.execute(text("UPDATE run_findings SET detectors_version = 0"))
    await queue_a_newer_analysis(api, run_id)
    loops = next(
        d for d in (await findings(api, run_id))["health"]["dimensions"] if d["name"] == "loops"
    )
    assert loops["status"] == "no_data" and loops["as_of"] is None
