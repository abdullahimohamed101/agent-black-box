"""The root agent: plans, delegates to a researcher and a coder in threads, then a reviewer as a task."""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from agent_blackbox import BlackBox, bind

from multi_agent import workers
from multi_agent.fake_model import FakeModel

# Planted so the tests can prove the redactor runs on delegation payloads (PayloadMode.FULL only).
PLANTED_SECRET = "AKIAIOSFODNN7EXAMPLE"  # noqa: S105 (AWS's documented example key id)


@dataclass
class Outcome:
    run_id: str
    trace_id: str
    results: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_job(
    bb: BlackBox,
    *,
    skew_ms: float = 0.0,
    fail_tool: bool = False,
    pace: float = 1.0,
    payload_mode: str = "metadata_only",
    child_env: dict[str, str] | None = None,
) -> Outcome:
    model = FakeModel(pace)
    outcome = Outcome("", "")
    task = {"goal": "add session refresh", "credentials": PLANTED_SECRET}

    def in_thread(name: str, fn: Any, *args: Any, **kwargs: Any) -> threading.Thread:
        def target() -> None:
            try:
                outcome.results[name] = fn(*args, **kwargs)
            except Exception as exc:  # a failed worker is reported, it does not crash the planner
                outcome.errors[name] = type(exc).__name__

        # Threads do not inherit the SDK context: bind() carries the current run and span across.
        thread = threading.Thread(target=bind(target), name=name)
        thread.start()
        return thread

    with bb.run("Ship feature: multi-agent", agent_id="planner") as run:
        outcome.run_id, outcome.trace_id = run.run_id, run.trace_id
        workers._model_turn(run, model, "planner", "split the job")
        with run.span("delegate", kind="custom"):  # the delegating span the workers nest under
            threads = [
                in_thread("researcher", workers.researcher, run, model, task, fail_tool=fail_tool),
                in_thread(
                    "coder",
                    workers.coder,
                    run,
                    model,
                    task,
                    skew_ms=skew_ms,
                    payload_mode=payload_mode,
                    child_env=child_env or {},
                ),
            ]
            for thread in threads:
                thread.join()
            summary = {
                "researcher": outcome.results.get("researcher"),
                "coder": outcome.results.get("coder"),
            }
            outcome.results["reviewer"] = asyncio.run(workers.reviewer(run, model, summary))
        if outcome.errors:
            raise RuntimeError("a worker failed: " + ", ".join(sorted(outcome.errors)))
    return outcome
