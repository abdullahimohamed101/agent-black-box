"""The cooperating agents. Each runs inside `run.agent(...)`, so its events carry its own agent id."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_blackbox import TRACE_CONTEXT_ENV, Run

from multi_agent.fake_model import FakeModel

TESTER = Path(__file__).resolve().parent / "tester_proc.py"
# Only what the child needs. `env={**os.environ, ...}` would also hand it every other secret you have.
_CHILD_ENV_KEYS = (
    "PATH",
    "BLACKBOX_API_KEY",
    "BLACKBOX_ENDPOINT",
    "BLACKBOX_MODE",
    "BLACKBOX_LOCAL_PATH",
)


def _model_turn(run: Run, model: FakeModel, agent: str, prompt: str) -> str:
    with run.llm_call(model.provider, model.name) as call:
        reply = model.complete(agent, prompt)
        call.record_usage(reply.input_tokens, reply.output_tokens, cost_usd=reply.cost_usd)
        call.set_payload({"response": reply.text})  # kept only in PayloadMode.FULL, redacted
    return reply.text


def researcher(run: Run, model: FakeModel, task: dict[str, Any], *, fail_tool: bool) -> str:
    with run.agent("researcher", input=task) as agent:
        _model_turn(run, model, "researcher", "plan the search")
        notes = 3
        try:
            with run.span("search_docs", kind="tool", attributes={"tool.name": "search_docs"}):
                model.pause(0.3)
                if fail_tool:
                    raise ConnectionError("docs index unreachable")  # recorded as tool.call.failed
        except ConnectionError:
            notes = 1  # the agent recovers and carries on with what it has
        agent.set_result({"notes": notes})
        return f"{notes} notes"


def child_environment(context: str, extra: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in _CHILD_ENV_KEYS if k in os.environ}
    env.update(extra)
    env[TRACE_CONTEXT_ENV] = context  # re-exported per child, never inherited by accident
    return env


def coder(
    run: Run,
    model: FakeModel,
    task: dict[str, Any],
    *,
    skew_ms: float,
    payload_mode: str,
    child_env: dict[str, str],
) -> str:
    with run.agent("coder", input=task) as agent:
        _model_turn(run, model, "coder", "write the patch")
        # Delegate to a tester in another process: agent.spawned now, a context string for the child.
        context = run.spawn_context("tester", input={"command": "pytest -q"})
        command = [
            sys.executable,
            str(TESTER),
            "--pace",
            str(model.pace),
            "--payload-mode",
            payload_mode,
        ]
        if skew_ms:
            command += ["--skew-ms", str(skew_ms)]
        result = subprocess.run(
            command,
            env=child_environment(context, child_env),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        agent.set_result({"tests_passed": result.returncode == 0})
        if result.returncode != 0:
            raise RuntimeError(f"tester exited {result.returncode}: {result.stderr[-300:]}")
        return "tests passed"


async def reviewer(run: Run, model: FakeModel, summary: dict[str, Any]) -> str:
    async with run.agent("reviewer", input=summary) as agent:
        # Runs as an asyncio task: tasks inherit the SDK context, and to_thread copies it, so no bind().
        text = await asyncio.to_thread(_model_turn, run, model, "reviewer", "review the patch")
        agent.set_result({"approved": True})
        return text
