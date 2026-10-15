"""KI-082 (ADR-158): permits are a fair share per workspace and per actor, refusals are
`SEARCH_BUSY` with a fixed reason, and the accounting returns to zero on every path."""

import asyncio
import uuid

import pytest

from abb_api.core.errors import AppError
from abb_api.search.reads import SearchGate

WS_A, WS_B, WS_C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Holder:
    """Holds permits open until released, so a test can pile them up."""

    def __init__(self, gate: SearchGate) -> None:
        self.gate = gate
        self.release = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []

    async def _hold(self, workspace: uuid.UUID, actor: str) -> None:
        async with self.gate.request(workspace, actor):
            await self.release.wait()

    async def take(self, workspace: uuid.UUID, actor: str) -> None:
        self.tasks.append(asyncio.create_task(self._hold(workspace, actor)))
        await asyncio.sleep(0)  # let it be admitted (or fail) before the next call
        await asyncio.sleep(0)

    async def finish(self) -> None:
        self.release.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def refused_reason(gate: SearchGate, workspace: uuid.UUID, actor: str) -> str | None:
    """The `reason` of a refusal, or None when the search was admitted."""
    try:
        async with gate.request(workspace, actor):
            return None
    except AppError as error:
        assert error.code == "SEARCH_BUSY" and error.headers == {"Retry-After": "1"}
        assert error.details is not None
        return str(error.details["reason"])


@pytest.fixture
def gate() -> SearchGate:
    return SearchGate(max_concurrent=4, timeout_seconds=1.0)  # actor 2, share 2, lone 3


async def test_one_noisy_actor_cannot_take_every_permit(gate: SearchGate) -> None:
    holder = Holder(gate)
    await holder.take(WS_A, "key:noisy")
    await holder.take(WS_A, "key:noisy")
    assert gate.held == 2
    assert await refused_reason(gate, WS_A, "key:noisy") == "actor_limit"
    assert gate.held == 2  # a refusal takes nothing
    await holder.finish()
    assert gate.held == 0


async def test_another_actor_and_another_workspace_still_get_a_permit_during_a_flood(
    gate: SearchGate,
) -> None:
    holder = Holder(gate)
    await holder.take(WS_A, "key:noisy")
    await holder.take(WS_A, "key:noisy")
    assert await refused_reason(gate, WS_A, "key:noisy") == "actor_limit"
    # A colleague in the same workspace is admitted while the workspace is alone (lone limit 3)...
    assert await refused_reason(gate, WS_A, "user:colleague") is None
    # ...and another workspace always finds a permit: the flood cannot reach the last one.
    await holder.take(WS_A, "user:colleague")
    assert await refused_reason(gate, WS_A, "user:third") == "workspace_share"
    assert await refused_reason(gate, WS_B, "user:other") is None
    await holder.take(WS_B, "user:other")
    assert gate.held == 4
    assert await refused_reason(gate, WS_C, "user:late") == "process_full"
    await holder.finish()
    assert gate.held == 0


async def test_a_workspace_is_held_to_half_once_someone_else_is_searching(
    gate: SearchGate,
) -> None:
    holder = Holder(gate)
    await holder.take(WS_B, "user:other")
    await holder.take(WS_A, "key:one")
    await holder.take(WS_A, "key:one")
    assert await refused_reason(gate, WS_A, "key:two") == "workspace_share"  # share is 2 of 4
    await holder.finish()


async def test_a_lone_user_is_not_refused_within_the_stated_share(gate: SearchGate) -> None:
    holder = Holder(gate)
    await holder.take(WS_A, "user:alone")
    assert await refused_reason(gate, WS_A, "user:alone") is None  # second permit of the actor
    await holder.take(WS_A, "user:alone")
    assert await refused_reason(gate, WS_A, "user:alone") == "actor_limit"
    await holder.finish()


@pytest.mark.parametrize(
    ("cap", "actor", "share", "lone"), [(1, 1, 1, 1), (2, 1, 1, 1), (4, 2, 2, 3), (14, 2, 7, 13)]
)
def test_the_limits_for_each_cap(cap: int, actor: int, share: int, lone: int) -> None:
    sized = SearchGate(max_concurrent=cap, timeout_seconds=1.0)
    assert (sized._per_actor, sized._share, sized._lone) == (actor, share, lone)


@pytest.mark.parametrize("how", ["error", "cancel", "timeout"])
async def test_accounting_returns_to_zero_on_every_path(gate: SearchGate, how: str) -> None:
    async def body() -> None:
        async with gate.request(WS_A, "user:a") as budget:
            assert budget.remaining_seconds() > 0
            assert gate.held == 1
            if how == "error":
                raise RuntimeError("boom")
            await asyncio.sleep(30)

    if how == "error":
        with pytest.raises(RuntimeError):
            await body()
    elif how == "cancel":
        task = asyncio.create_task(body())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await body()
    assert gate.held == 0
    assert gate._by_workspace == {} and gate._by_actor == {}  # nothing left behind
    assert await refused_reason(gate, WS_A, "user:a") is None


async def test_a_refused_search_leaves_no_trace(gate: SearchGate) -> None:
    holder = Holder(gate)
    await holder.take(WS_A, "key:noisy")
    await holder.take(WS_A, "key:noisy")
    for _ in range(50):
        assert await refused_reason(gate, WS_A, "key:noisy") == "actor_limit"
    await holder.finish()
    assert gate.held == 0 and gate._by_workspace == {} and gate._by_actor == {}
