#!/usr/bin/env python3
"""A small scripted support agent, recorded with Agent BlackBox: one tool failure, one retry, a good ending.

    uv run --project packages/sdk-python python examples/python/scripted_agent.py            # scripted, deterministic
    uv run --project packages/sdk-python python examples/python/scripted_agent.py --offline  # nothing leaves the process
    ANTHROPIC_API_KEY=... uv run --project packages/sdk-python python examples/python/scripted_agent.py --live

The scripted model plays a fixed plan with fixed token counts and a fixed (made-up) price table, so the run
is the same every time and needs no network and no model key. The story: the agent plans, looks a ticket up,
the ticket service times out (the tool span is recorded as failed), the agent retries, the lookup works, and
the model writes the answer. `--live` swaps only the model for a real Anthropic call (the ticket lookup stays
scripted and runs no commands). The project has not run `--live` (KI-044).

Needs BLACKBOX_API_KEY (a project key with events:write) and BLACKBOX_ENDPOINT, or --offline. Prints one JSON
line with the run id.
"""

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from blackbox import BlackBox, PayloadMode, Run

TICKET_ID = "T-1042"
TICKET = {
    "id": TICKET_ID,
    "title": "Cannot reset password",
    "status": "open",
    "customer": "Acme Co",
}
# Made-up prices (USD per million tokens) so the cost column has stable numbers. Not any provider's rate.
PRICE_IN, PRICE_OUT = 3.0, 15.0


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int
    output_tokens: int


class Model(Protocol):
    provider: str
    name: str

    def complete(self, step: int, prompt: str) -> Reply: ...


class ScriptedModel:
    """Fixed replies and token counts, indexed by step: no randomness, no network."""

    provider, name = "scripted", "scripted-small"
    _PLAN = (
        Reply("I will look up the ticket first.", 420, 18),
        Reply(
            f"Ticket {TICKET_ID} ({TICKET['title']}, {TICKET['customer']}) is open. "
            "Suggested reply: send the password reset link and ask them to try again.",
            610,
            52,
        ),
    )

    def complete(self, step: int, prompt: str) -> Reply:
        return self._PLAN[step]


class AnthropicLive:
    """One plain Messages API call over the standard library. Not run by the project's CI (KI-044)."""

    provider = "anthropic"

    def __init__(self) -> None:
        self.name = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self._key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._key:
            raise RuntimeError("--live needs ANTHROPIC_API_KEY in the environment")

    def complete(self, step: int, prompt: str) -> Reply:
        body = json.dumps(
            {
                "model": self.name,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            data = json.loads(response.read())
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        return Reply(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))


def cost(reply: Reply) -> float:
    return round((reply.input_tokens * PRICE_IN + reply.output_tokens * PRICE_OUT) / 1e6, 6)


def lookup_ticket(attempt: int) -> dict[str, Any]:
    """The scripted tool: the first call times out, the second works."""
    if attempt == 1:
        raise TimeoutError("ticket service did not answer within 5 s")
    return TICKET


def think(run: Run, model: Model, step: int, prompt: str) -> Reply:
    with run.llm_call(model.provider, model.name) as call:
        reply = model.complete(step, prompt)
        call.record_usage(reply.input_tokens, reply.output_tokens, cost_usd=cost(reply))
        call.set_payload({"response": reply.text})  # kept only with full payload capture, redacted
    return reply


def run_agent(run: Run, model: Model, *, max_attempts: int = 3) -> dict[str, Any]:
    think(run, model, 0, f"Plan how to answer support ticket {TICKET_ID}. One sentence.")
    ticket: dict[str, Any] | None = None
    retries = 0
    for attempt in range(1, max_attempts + 1):
        try:
            with run.span(
                "lookup-ticket", kind="tool"
            ) as span:  # an exception marks the span failed
                span.set_attribute("tool.ticket_id", TICKET_ID)
                ticket = lookup_ticket(attempt)
            break
        except TimeoutError as exc:
            retries += 1
            run.event(
                "retry.attempted",
                {"retry.attempt": retries, "retry.reason": f"lookup-ticket failed: {exc}"},
            )
    if ticket is None:
        raise RuntimeError("the ticket lookup kept failing")
    answer = think(
        run,
        model,
        1,
        f"Ticket: {json.dumps(ticket)}. Write a two-sentence status and a suggested reply.",
    )
    return {"answer": answer.text, "retries": retries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="use a real Anthropic model (needs ANTHROPIC_API_KEY and network; not run by the project)",
    )
    parser.add_argument(
        "--offline", action="store_true", help="record nothing remotely (events stay in memory)"
    )
    args = parser.parse_args(argv)
    model: Model = AnthropicLive() if args.live else ScriptedModel()
    bb = BlackBox(
        project="custom-agent-demo",
        agent_id="support-agent",
        mode="offline" if args.offline else "http",
        payload_mode=PayloadMode.METADATA_ONLY,
    )
    with bb.run(f"Answer support ticket {TICKET_ID}", metadata={"model": model.name}) as run:
        run_id = run.run_id
        outcome = run_agent(run, model)
    flushed = bb.shutdown(15)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "retries": outcome["retries"],
                "flushed": flushed,
                "stats": bb.stats(),
            }
        )
    )
    return 0 if (flushed or args.offline) else 1


if __name__ == "__main__":
    sys.exit(main())
