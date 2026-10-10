"""Deterministic pathological runs for the reliability detectors (Phase 10).

Ten scenarios, each a run (or, for `cost_spike`, 26 runs) of wire events with fixed ids and
timestamps, so the committed fixtures never drift:

    healthy         an ordinary run: every detector's negative side (no finding is expected)
    looping         search/read/reason x 11, then `read` x 9, plus a loop reported by an integration
    identical       one tool called 7 times with the same inline payload
    retry_storm     9 retries, 4 of them on one operation within 16 s
    context_growth  7 model calls whose input grows from 8k to 120k tokens, faster and faster
    two_agents      two emitters interleaved; only agent-a loops
    skewed          two emitters whose clocks differ by 5 minutes
    cost_spike      25 cheap runs of one agent, then one that costs 45 times as much
    cascade         a database query fails 4 times in 9 calls with 3 retries, then a different tool
    failing         file change -> failing tests -> retry -> command exit 2 -> run.failed

Usage (from apps/api, so the event schema is importable):

    uv run python ../scripts/pathological_runs.py --out tests/fixtures/pathological
    uv run python ../scripts/pathological_runs.py --check tests/fixtures/pathological
    uv run python ../scripts/pathological_runs.py --api http://localhost:8000 --key-file KEY \
        [--shuffle SEED]

`*.expected.json` next to the fixtures are written by hand from the scenarios above (they refer to
events by `sequence`); this script never writes them.
"""

import argparse
import hashlib
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from abb_event_schema.ids import IdGenerator, IdKind
from abb_event_schema.parse import parse_event_in

BASE = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)  # after the built-in prices take effect
FIXED_MS = int(BASE.timestamp() * 1000)
MODEL = {"llm.provider": "anthropic", "llm.model": "claude-sonnet-5-5"}
Event = dict[str, Any]


class Run:
    """Builds the events of one run. Time advances only through `dt` and `gap`."""

    def __init__(
        self,
        name: str,
        *,
        agent: str = "coder",
        at: datetime = BASE,
        skew: dict[str, float] | None = None,
    ) -> None:
        digest = hashlib.sha256(name.encode()).digest()
        self._ids = IdGenerator(clock_ms=lambda: FIXED_MS, entropy=lambda n: digest[:n])
        self.run_id = self._ids.new_id(IdKind.RUN)
        self.trace_id = self._ids.new_id(IdKind.TRACE)
        self.agent = agent
        self.now = at
        self.seq = 0
        self.skew = skew or {}
        self.events: list[Event] = []
        self.root: dict[str, str] = {}

    # ----------------------------------------------------------------- primitives

    def gap(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def span(self) -> str:
        return self._ids.new_id(IdKind.SPAN)

    def emit(
        self,
        event_type: str,
        *,
        agent: str | None = None,
        span: str | None = None,
        parent: str | None = None,
        status: str | None = None,
        attrs: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        dt: float = 0.5,
        duration_ms: float | None = None,
    ) -> Event:
        agent = agent or self.agent
        self.gap(dt)
        self.seq += 1
        occurred = self.now + timedelta(seconds=self.skew.get(agent, 0.0))
        event: Event = {
            "schema_version": "1.0",
            "event_id": self._ids.new_id(IdKind.EVENT),
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "span_id": span,
            "parent_span_id": parent,
            "agent_id": agent,
            "event_type": event_type,
            "occurred_at": occurred.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "sequence": self.seq,
            "status": status,
            "duration_ms": duration_ms,
            "attributes": attrs or {},
            "payload": payload,
        }
        event = {k: v for k, v in event.items() if v is not None}
        self.events.append(event)
        return event

    # ----------------------------------------------------------------- vocabulary

    def start(self, *agents: str) -> None:
        self.emit("run.started", attrs={"run.name": "pathological"})
        for agent in agents or (self.agent,):
            self.root[agent] = self.span()
            self.emit("agent.started", agent=agent, span=self.root[agent])

    def finish(self, *, ok: bool = True) -> None:
        for agent, root in self.root.items():
            self.emit(
                "agent.completed", agent=agent, span=root, status="success" if ok else "error"
            )
        self.emit(
            "run.completed" if ok else "run.failed",
            agent=self.agent,
            status="success" if ok else None,
        )

    def llm(
        self,
        input_tokens: int,
        output_tokens: int = 200,
        *,
        agent: str | None = None,
        span: str | None = None,
        retries_before_close: int = 0,
    ) -> Event:
        """A model call; `retries_before_close` rate-limited retries happen inside its span."""
        agent = agent or self.agent
        span = span or self.span()
        parent = self.root[agent]
        self.emit("llm.request.started", agent=agent, span=span, parent=parent, attrs=dict(MODEL))
        for attempt in range(1, retries_before_close + 1):
            self.emit("rate_limit.hit", agent=agent, span=span, parent=parent, dt=2)
            self.retry(span, attempt, agent=agent, dt=2)
        attrs = {**MODEL, "llm.input_tokens": input_tokens, "llm.output_tokens": output_tokens}
        return self.emit(
            "llm.request.completed",
            agent=agent,
            span=span,
            parent=parent,
            status="success",
            attrs=attrs,
            duration_ms=900,
        )

    def tool(
        self,
        name: str,
        *,
        ok: bool = True,
        error_type: str = "tool_error",
        payload: dict[str, Any] | None = None,
        agent: str | None = None,
        dt: float = 1.0,
    ) -> Event:
        agent = agent or self.agent
        span, parent = self.span(), self.root[agent]
        self.emit(
            "tool.call.started", agent=agent, span=span, parent=parent, attrs={"tool.name": name},
            payload=payload, dt=dt / 2,
        )  # fmt: skip
        if ok:
            return self.emit(
                "tool.call.completed", agent=agent, span=span, parent=parent, status="success",
                attrs={"tool.name": name}, duration_ms=dt * 500, dt=dt / 2,
            )  # fmt: skip
        return self.emit(
            "tool.call.failed", agent=agent, span=span, parent=parent, status="error",
            attrs={"tool.name": name, "tool.error_type": error_type}, duration_ms=dt * 500, dt=dt / 2,
        )  # fmt: skip

    def shell(
        self, command: str, exit_code: int = 0, *, tests_failed: int | None = None, dt: float = 2.0
    ) -> Event:
        span, parent = self.span(), self.root[self.agent]
        attrs: dict[str, Any] = {"shell.command": command}
        self.emit("shell.command.started", span=span, parent=parent, attrs=attrs, dt=dt / 2)
        done = {**attrs, "shell.exit_code": exit_code}
        if tests_failed is not None:
            done["test.failed"] = tests_failed
        return self.emit(
            "shell.command.completed" if exit_code == 0 else "shell.command.failed",
            span=span, parent=parent, status="success" if exit_code == 0 else "error",
            attrs=done, duration_ms=dt * 500, dt=dt / 2,
        )  # fmt: skip

    def db(self, *, ok: bool = True, dt: float = 1.0) -> Event:
        span, parent = self.span(), self.root[self.agent]
        self.emit(
            "db.query.started",
            span=span,
            parent=parent,
            attrs={"db.operation": "select"},
            dt=dt / 2,
        )
        return self.emit(
            "db.query.completed" if ok else "db.query.failed",
            span=span, parent=parent, status="success" if ok else "error",
            attrs={"db.operation": "select"}, duration_ms=dt * 500, dt=dt / 2,
        )  # fmt: skip

    def retry(
        self, span: str | None, attempt: int, *, agent: str | None = None, dt: float = 0.5
    ) -> Event:
        attrs = {"retry.attempt": attempt, "retry.reason": "transient"}
        return self.emit("retry.attempted", agent=agent, span=span, attrs=attrs, dt=dt)

    def file_modified(self, path: str) -> Event:
        return self.emit("file.modified", attrs={"file.path": path}, dt=0.5)


# --------------------------------------------------------------------------- scenarios


def healthy() -> list[Run]:
    r = Run("healthy", at=BASE)
    r.start()
    r.llm(8_000)
    r.tool("search")
    r.llm(10_000)
    r.tool("read")
    r.llm(12_000)
    r.file_modified("src/app.py")
    r.llm(14_000)
    r.shell("pytest -q", 1, tests_failed=2)
    r.llm(16_000)
    failed = r.tool("fetch", ok=False, error_type="connection_reset")
    r.gap(120)
    r.retry(failed["span_id"], 1)
    r.tool("fetch")
    r.llm(18_000)
    r.shell("pytest -q", 0)
    r.llm(21_000)
    slow = r.tool("summarize")
    r.gap(300)
    r.retry(slow["span_id"], 1)
    r.llm(24_000)
    r.finish()
    return [r]


def looping() -> list[Run]:
    r = Run("looping", at=BASE + timedelta(hours=1))
    r.start()
    for i in range(11):
        r.tool("search")
        r.tool("read")
        r.llm(12_000 + 50 * i)
    r.emit(
        "loop.detected",
        attrs={"loop.pattern": ["search", "read", "reason"], "loop.repetitions": 11},
    )
    for _ in range(9):
        r.tool("read")
    r.finish()
    return [r]


def identical() -> list[Run]:
    r = Run("identical", at=BASE + timedelta(hours=2))
    r.start()
    r.llm(9_000)
    r.tool("plan")
    for _ in range(7):
        r.tool("fetch_page", payload={"url": "https://example.test/a"})
    r.llm(9_100)
    for _ in range(2):
        r.tool("summarize", payload={"text": "same"})
    r.tool("summarize", payload={"text": "different"})
    r.finish()
    return [r]


def retry_storm() -> list[Run]:
    r = Run("retry_storm", at=BASE + timedelta(hours=3))
    r.start()
    r.llm(10_000, retries_before_close=4)  # 4 retries inside 16 s
    steps = ["plan", "read", "edit", "test", "lint"]
    for step in steps:
        r.tool(step)
        r.gap(90)
        r.llm(10_000, retries_before_close=1)  # a spaced single retry each
    r.finish()
    return [r]


def context_growth() -> list[Run]:
    r = Run("context_growth", at=BASE + timedelta(hours=4))
    r.start()
    sizes = [8_000, 9_000, 12_000, 20_000, 38_000, 70_000, 120_000]
    tools = ["plan", "read", "edit", "test", "lint", "format"]
    for i, size in enumerate(sizes):
        r.llm(size)
        if i < len(tools):
            r.tool(tools[i])
    r.finish()
    return [r]


def two_agents() -> list[Run]:
    r = Run("two_agents", agent="agent-a", at=BASE + timedelta(hours=5))
    r.start("agent-a", "agent-b")
    steps = ["plan", "write", "lint", "build", "deploy", "notify"]
    for step in steps:
        r.tool("search", agent="agent-a")
        r.tool(step, agent="agent-b")
        r.tool("read", agent="agent-a")
    r.finish()
    return [r]


def skewed() -> list[Run]:
    r = Run("skewed", agent="agent-a", at=BASE + timedelta(hours=6), skew={"agent-b": 300.0})
    r.start("agent-a", "agent-b")
    span_a = r.llm(10_000, agent="agent-a", retries_before_close=0)["span_id"]
    span_b = r.llm(10_000, agent="agent-b", retries_before_close=0)["span_id"]
    for attempt in range(1, 4):  # three retries by A within 30 s of A's clock
        r.retry(span_a, attempt, agent="agent-a", dt=10)
        if attempt <= 2:  # and two by B, 300 s ahead on its own clock
            r.retry(span_b, attempt, agent="agent-b", dt=1)
    r.tool("read", agent="agent-a")
    r.tool("write", agent="agent-b")
    r.finish()
    return [r]


def cost_spike() -> list[Run]:
    runs: list[Run] = []
    for i in range(25):
        r = Run(f"cost_spike_{i}", agent="spiky", at=BASE + timedelta(hours=7, minutes=10 * i))
        r.start()
        cheap = i < 20
        r.llm(8_000 if cheap else 12_000, 400 if cheap else 600)
        r.finish()
        runs.append(r)
    big = Run("cost_spike_big", agent="spiky", at=BASE + timedelta(hours=7, minutes=10 * 25))
    big.start()
    big.tool("plan")
    big.llm(400_000, 10_000)
    big.finish()
    runs.append(big)
    return runs


def cascade() -> list[Run]:
    r = Run("cascade", at=BASE + timedelta(hours=13))
    r.start()
    r.llm(9_000)
    r.db()  # q1
    failed = [r.db(ok=False)["span_id"]]  # q2
    r.retry(failed[-1], 1)
    failed.append(r.db(ok=False)["span_id"])  # q3
    r.retry(failed[-1], 1)
    r.llm(9_100)
    r.db()  # q4
    failed.append(r.db(ok=False)["span_id"])  # q5
    r.retry(failed[-1], 1)
    r.llm(9_200)
    r.db()  # q6
    r.db()  # q7
    r.db(ok=False)  # q8
    r.db()  # q9
    r.llm(9_300)
    r.tool("fallback")
    r.finish()
    return [r]


def failing() -> list[Run]:
    r = Run("failing", at=BASE + timedelta(hours=14))
    r.start()
    r.llm(8_000)
    r.tool("read")
    r.file_modified("src/app.py")
    r.llm(8_500)
    r.shell("pytest -q", 1, tests_failed=3)
    second = r.span()
    r.retry(second, 1)
    r.emit(
        "shell.command.started",
        span=second,
        parent=r.root[r.agent],
        attrs={"shell.command": "pytest -q"},
    )
    r.emit(
        "shell.command.failed", span=second, parent=r.root[r.agent], status="error",
        attrs={"shell.command": "pytest -q", "shell.exit_code": 2}, duration_ms=1000,
    )  # fmt: skip
    r.emit("run.failed")
    return [r]


SCENARIOS = {
    "healthy": healthy,
    "looping": looping,
    "identical": identical,
    "retry_storm": retry_storm,
    "context_growth": context_growth,
    "two_agents": two_agents,
    "skewed": skewed,
    "cost_spike": cost_spike,
    "cascade": cascade,
    "failing": failing,
}


def render() -> dict[str, str]:
    """name -> NDJSON text (one wire event per line), validated against the event schema."""
    out: dict[str, str] = {}
    for name, build in SCENARIOS.items():
        lines = []
        for run in build():
            for event in run.events:
                parse_event_in(event)  # the fixtures are valid wire events by construction
                lines.append(json.dumps(event, separators=(",", ":"), sort_keys=True))
        out[name] = "\n".join(lines) + "\n"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, help="write <name>.ndjson files here")
    parser.add_argument(
        "--check", type=Path, help="exit 1 if the files here differ from a regeneration"
    )
    parser.add_argument("--api", help="post the events to this API (e.g. http://localhost:8000)")
    parser.add_argument("--key-file", type=Path, help="file holding an API key with event.write")
    parser.add_argument("--shuffle", type=int, help="shuffle each posted batch with this seed")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    rendered = render()

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, text in rendered.items():
            (args.out / f"{name}.ndjson").write_text(text)
        print(f"wrote {len(rendered)} fixtures to {args.out}")
    if args.check:
        stale = [
            n for n, text in rendered.items()
            if not (args.check / f"{n}.ndjson").exists() or (args.check / f"{n}.ndjson").read_text() != text
        ]  # fmt: skip
        if stale:
            print(f"fixtures out of date: {', '.join(stale)}", file=sys.stderr)
            return 1
        print(f"{len(rendered)} fixtures are current")
    if args.api:
        if args.key_file is None:
            parser.error("--api needs --key-file")
        import httpx

        key = args.key_file.read_text().strip()
        events = [json.loads(line) for text in rendered.values() for line in text.splitlines()]
        rng = random.Random(args.shuffle)
        with httpx.Client(
            base_url=args.api, headers={"Authorization": f"Bearer {key}"}, timeout=60
        ) as client:
            for start in range(0, len(events), args.batch_size):
                batch = events[start : start + args.batch_size]
                if args.shuffle is not None:
                    rng.shuffle(batch)
                response = client.post("/v1/events/batch", json={"events": batch})
                response.raise_for_status()
        print(f"posted {len(events)} events to {args.api}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
