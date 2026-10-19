"""A virtual clock for the scripted demo agents (`make demo`): plausible timestamps and durations without waiting.

The SDK stamps every event with the wall clock and measures spans with a monotonic clock; it has no clock setting. The scripted
agents therefore patch their OWN process's two clock functions (the precedent is examples/multi-agent/multi_agent/tester_proc.py):

* `agent_blackbox.events.now_rfc3339` gives each event's `occurred_at`: here, "`started_ago` seconds ago" plus real elapsed time plus
  every virtual pause so far;
* `agent_blackbox.client.time` is shadowed by a shim whose `monotonic()` includes the same pauses, so span and run durations agree.

Everything is still sent through the SDK and the ingestion API like any other run. Nothing is written to the database
directly. The agent calls `clock.advance(seconds)` where a real agent would be waiting for a model or a tool.
"""

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import agent_blackbox.client
import agent_blackbox.events

# Made-up prices (USD per million tokens: input, output) for the scripted models. Not any provider's rate.
SCRIPTED_PRICES: dict[str, tuple[float, float]] = {
    "scripted-small": (3.0, 15.0),
    "scripted-large": (9.0, 45.0),
}


def scripted_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = SCRIPTED_PRICES[model]
    return round((tokens_in * price_in + tokens_out * price_out) / 1e6, 6)


def latency_seconds(output_tokens: int) -> float:
    """A believable model latency: a second and a half to start, then about thirty tokens per second."""
    return 1.5 + output_tokens / 30.0


class _TimeShim:
    """`time` as client.py sees it: the monotonic clock includes the pauses; everything else is the real module."""

    def __init__(self, clock: "DemoClock") -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return time.monotonic() + self._clock.offset

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


class DemoClock:
    """Install with `DemoClock(started_ago=3600)`: events from now on carry timestamps from an hour ago."""

    def __init__(self, started_ago: float = 0.0) -> None:
        self.offset = 0.0
        self._real0 = time.time()
        self._base = self._real0 - started_ago
        agent_blackbox.events.now_rfc3339 = self._rfc3339
        agent_blackbox.client.time = _TimeShim(self)  # type: ignore[assignment]

    def now(self) -> float:
        return self._base + (time.time() - self._real0) + self.offset

    def _rfc3339(self) -> str:
        moment = datetime.fromtimestamp(self.now(), timezone.utc)
        return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def advance(self, seconds: float) -> None:
        """Let `seconds` of pretend time pass, instantly."""
        if seconds > 0:
            self.offset += seconds


def hours(h: float = 0.0, minutes: float = 0.0, days: float = 0.0) -> float:
    """Seconds for `--started-ago`: `hours(3, 20)` is three hours and twenty minutes."""
    return ((days * 24 + h) * 60 + minutes) * 60


class LatentModel:
    """Wraps a scripted model so every `complete` takes latency_seconds(output tokens) of virtual time.

    `complete` runs inside the SDK's llm_call span, so the span (and the run) last as long as a real call would."""

    def __init__(self, model: Any, clock: DemoClock, tokens_out: Callable[[Any], int]) -> None:
        self._model, self._clock, self._tokens_out = model, clock, tokens_out
        self.provider, self.name = model.provider, model.name

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        reply = self._model.complete(*args, **kwargs)
        self._clock.advance(latency_seconds(self._tokens_out(reply)))
        return reply
