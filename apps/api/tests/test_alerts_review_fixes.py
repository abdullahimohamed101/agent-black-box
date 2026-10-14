"""Fixes from the Phase 17 adversarial review that need their own scenario (the evaluation ones
live in test_alerts_evaluation.py, the sweep chain in test_alerts_sweep_chain.py)."""

import asyncio
import contextlib
from collections import Counter
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from abb_api.alerts.delivery import deliver_due
from abb_api.alerts.notifiers import SendResult
from abb_api.alerts.repository import DestinationRepository
from tests.alerts_helpers import seed_destination, seed_rule
from tests.api_fixtures import Api
from tests.test_alerts_delivery import T0, dsettings, pending


class _Ok:
    async def send(self, destination: object, alert: object) -> SendResult:
        return SendResult(True)


async def test_concurrent_ticks_apply_destination_health_in_one_order_and_do_not_deadlock(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P2-5: the tick of `<ws>` and the tick of `<ws>:retry` can overlap. Each locks the
    destinations it touched one by one; in dictionary order two ticks that touched A,B and B,A
    deadlock."""
    tenant = api.tenant.context
    a = await seed_destination(api.engine, tenant, name="a")
    b = await seed_destination(api.engine, tenant, name="b", url="https://example.org/h")
    rule = await seed_rule(api.engine, tenant, [a, b])
    from datetime import timedelta

    for offset, destination in enumerate((a, b, b, a)):  # tick 1 gets A,B and tick 2 gets B,A
        await pending(api, rule, destination, at=T0 + timedelta(seconds=offset))
    settings = dsettings(alerts_deliver_batch=2)
    registry = {"webhook": _Ok(), "slack": _Ok(), "email": _Ok()}

    real = DestinationRepository.apply_health
    first_done: Counter[int] = Counter()
    both_locked_one = asyncio.Event()

    async def gated(self: DestinationRepository, destination_id: Any, **kwargs: Any) -> Any:
        out = await real(self, destination_id, **kwargs)
        key = id(self._conn)
        first_done[key] += 1
        if first_done[key] == 1:
            if len(first_done) == 2:
                both_locked_one.set()
            # Let the other tick take its first lock too; with one lock order it queues behind this
            # tick instead, and this wait simply times out.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_locked_one.wait(), 0.8)
        return out

    monkeypatch.setattr(DestinationRepository, "apply_health", gated)

    async def tick() -> Any:
        async with api.engine.begin() as conn:
            return await deliver_due(
                conn,
                tenant,
                now=T0 + timedelta(minutes=1),
                settings=settings,
                registry=registry,  # type: ignore[arg-type]
            )

    first = asyncio.create_task(tick())
    await asyncio.sleep(0.4)  # the first tick has claimed its two rows
    second = asyncio.create_task(tick())
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 30)
    assert not [r for r in results if isinstance(r, DBAPIError)], results
    assert sorted(r.delivered for r in results) == [2, 2]
