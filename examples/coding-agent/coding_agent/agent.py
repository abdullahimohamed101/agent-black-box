"""The agent loop: model call, tool calls, repeat. Every step is a typed Agent Black Box event."""

from dataclasses import dataclass
from typing import Any

from blackbox import BlackBox, Run
from blackbox.coding import CodingRecorder

from coding_agent.models import Model
from coding_agent.tools import TOOL_SCHEMAS, Toolbox, ToolError

ISSUE = (
    "Users are being logged out early. Sessions should be refreshed shortly before they expire and "
    "must keep working after a refresh. Fix the bug in this repository, make the tests pass, "
    "and push your fix on a branch."
)


@dataclass
class AgentResult:
    final_text: str
    turns: int
    test_runs: int
    retries: int
    tests_passed: bool


def run_agent(
    bb: BlackBox,
    run: Run,
    rec: CodingRecorder,
    model: Model,
    *,
    issue: str = ISSUE,
    max_turns: int = 12,
) -> AgentResult:
    tools = Toolbox(rec)
    messages: list[dict[str, Any]] = [{"role": "user", "content": issue}]
    test_runs = retries = 0
    last_tests_failed: tuple[str, ...] | None = None
    passed = False
    final = ""
    turns = 0
    for _ in range(max_turns):
        turns += 1
        with run.llm_call(model.provider, model.name) as call:
            reply = model.complete(messages, TOOL_SCHEMAS)
            call.record_usage(reply.input_tokens, reply.output_tokens, cost_usd=reply.cost_usd)
            call.set_payload({"response": reply.text})  # kept only in PayloadMode.FULL, redacted
        messages.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls})
        final = reply.text
        if not reply.tool_calls:
            break
        results = []
        for tc in reply.tool_calls:
            if tc.name == "edit_file" and last_tests_failed is not None:
                retries += 1  # editing again after a failing run is the retry
                run.event(
                    "retry.attempted",
                    {
                        "retry.attempt": retries,
                        "retry.reason": "tests failed: " + ", ".join(last_tests_failed)[:300],
                    },
                )
                last_tests_failed = None
            try:
                with run.span(tc.name, kind="tool"):  # an exception here marks the span failed
                    outcome = tools.call(tc.name, tc.args)
            except (ToolError, OSError, ValueError) as exc:
                results.append({"id": tc.id, "text": rec.sanitize(f"error: {exc}")})
                continue
            if tc.name == "run_tests":
                test_runs += 1
                passed = bool(outcome.ok and not outcome.tests_failed)
                last_tests_failed = None if passed else (outcome.failing or ("unknown",))
            results.append({"id": tc.id, "text": outcome.text})
        messages.append({"role": "tool", "results": results})
    return AgentResult(final, turns, test_runs, retries, passed)
