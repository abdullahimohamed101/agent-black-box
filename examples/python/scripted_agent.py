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
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from blackbox import BlackBox, PayloadMode, Run

from demo_clock import SCRIPTED_PRICES, DemoClock, LatentModel, hours

TICKET_ID = "T-1042"
TICKET = {
    "id": TICKET_ID,
    "title": "Refund not received",
    "subject": "refund not received",
    "status": "open",
    "customer": "Acme Co",
}
# More tickets for `make demo`, so the runs list reads like a support team's week: the run is named after the
# ticket's `subject` ("Answer ticket: refund not received"); the id travels in the lookup payload, not the name.
TICKETS = {
    TICKET_ID: TICKET,
    "T-1043": {
        "id": "T-1043",
        "title": "Login loop on iOS",
        "subject": "login loop on iOS",
        "status": "open",
        "customer": "Acme Co",
    },
    "T-1044": {
        "id": "T-1044",
        "title": "Duplicate order emails",
        "subject": "duplicate order emails",
        "status": "open",
        "customer": "Acme Co",
    },
    "T-1045": {
        "id": "T-1045",
        "title": "Export stuck at 90 %",
        "subject": "export stuck at 90 %",
        "status": "open",
        "customer": "Acme Co",
    },
    "T-1046": {
        "id": "T-1046",
        "title": "Card declined twice",
        "subject": "card declined twice",
        "status": "open",
        "customer": "Acme Co",
    },
    "T-1047": {
        "id": "T-1047",
        "title": "Invoice shows wrong VAT",
        "subject": "invoice shows wrong VAT",
        "status": "open",
        "customer": "Acme Co",
    },
    "T-1048": {
        "id": "T-1048",
        "title": "Password reset not sent",
        "subject": "password reset not sent",
        "status": "open",
        "customer": "Acme Co",
    },
}
# What the ticket lookup does. `recovers` is the default story (one timeout, one retry, a good ending).
VARIANTS = ("recovers", "clean", "flaky", "outage", "denied")
# Made-up prices (USD per million tokens) so the cost column has stable numbers. Not any provider's rate.
PRICE_IN, PRICE_OUT = 3.0, 15.0


class ConnectionTimeout(TimeoutError):
    """The ticket service did not answer. A named type, so the run page can say what broke."""


class PermissionDenied(Exception):
    """The ticket service refused this agent's credentials: retrying cannot help."""


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

    provider = "scripted"

    def __init__(
        self,
        ticket: dict[str, Any] = TICKET,
        *,
        tokens_scale: float = 1.0,
        name: str = "scripted-small",
    ) -> None:
        """`tokens_scale` multiplies every token count: `make demo` uses real-sized contexts for believable costs."""
        self.name = name

        def reply(text: str, tokens_in: int, tokens_out: int) -> Reply:
            return Reply(text, round(tokens_in * tokens_scale), round(tokens_out * tokens_scale))

        self._plan = (
            reply("I will look up the ticket first.", 420, 18),
            reply(
                f"Ticket {ticket['id']} ({ticket['title']}, {ticket['customer']}) is open. "
                "Suggested reply: send the password reset link and ask them to try again.",
                610,
                52,
            ),
            reply("The ticket service is down. I will escalate to the on-call engineer.", 560, 40),
        )

    def complete(self, step: int, prompt: str) -> Reply:
        return self._plan[step]


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


def cost(reply: Reply, model: str | None = None) -> float:
    price_in, price_out = SCRIPTED_PRICES.get(model or "", (PRICE_IN, PRICE_OUT))
    return round((reply.input_tokens * price_in + reply.output_tokens * price_out) / 1e6, 6)


def lookup_ticket(
    attempt: int, ticket: dict[str, Any], variant: str = "recovers"
) -> dict[str, Any]:
    """The scripted tool. `recovers`: the first call times out, the second works. `flaky`: the first five time out.
    `outage`: it never answers. `denied`: it refuses at once. `clean`: it works."""
    failing_attempts = {"recovers": 1, "flaky": 5, "outage": 99}.get(variant, 0)
    if variant == "denied":
        raise PermissionDenied("the ticket service refused this agent's token")
    if attempt <= failing_attempts:
        raise ConnectionTimeout("ticket service did not answer within 5 s")
    return ticket


def think(run: Run, model: Model, step: int, prompt: str, pace: float = 0.0) -> Reply:
    with run.llm_call(model.provider, model.name) as call:
        time.sleep(
            0.5 * pace
        )  # a pretend model latency, so durations on the run page read like work
        reply = model.complete(step, prompt)
        call.record_usage(reply.input_tokens, reply.output_tokens, cost_usd=cost(reply, model.name))
        call.set_payload({"response": reply.text})  # kept only with full payload capture, redacted
    return reply


def run_agent(
    run: Run,
    model: Model,
    *,
    max_attempts: int = 3,
    ticket: dict[str, Any] = TICKET,
    variant: str = "recovers",
    pace: float = 0.0,
    clock: DemoClock | None = None,
) -> dict[str, Any]:
    """`clock`: a virtual clock (see demo_clock.py); tool waits and retry back-offs then pass instantly but are recorded."""
    think(run, model, 0, f"Plan how to answer support ticket {ticket['id']}. One sentence.", pace)
    found: dict[str, Any] | None = None
    retries = 0
    if variant == "flaky":
        max_attempts = max(max_attempts, 7)  # five timeouts, then it answers
    for attempt in range(1, max_attempts + 1):
        try:
            with run.span(
                "lookup-ticket", kind="tool"
            ) as span:  # an exception marks the span failed
                span.set_attribute("tool.ticket_id", ticket["id"])
                try:
                    found = lookup_ticket(attempt, ticket, variant)
                except (ConnectionTimeout, PermissionDenied) as exc:
                    span.set_attribute(
                        "tool.error_type", type(exc).__name__
                    )  # the run page's headline names it
                    if isinstance(exc, ConnectionTimeout):
                        run.event(
                            "timeout.occurred",
                            {"timeout.operation": "lookup-ticket", "timeout.limit_ms": 5000},
                        )
                    raise
                finally:
                    # a timeout lasts its 5 s limit; an answer takes a second or so
                    if clock is not None:
                        clock.advance(1.4 if sys.exc_info()[0] is None else 5.0)
                    else:
                        time.sleep((0.4 if variant == "clean" else 1.8) * pace)
            break
        except PermissionDenied:
            raise  # a refusal is not a flaky call: no retry
        except TimeoutError as exc:
            if attempt == max_attempts:
                break
            retries += 1
            if clock is not None:
                clock.advance(2.0)  # back-off before the next try
            run.event(
                "retry.attempted",
                {"retry.attempt": retries, "retry.reason": f"lookup-ticket failed: {exc}"},
            )
    if found is None:
        # The agent still has to tell someone: one more (scripted) model call, spent after the first error.
        think(
            run,
            model,
            2,
            f"Ticket {ticket['id']} cannot be read. Write a short escalation note.",
            pace,
        )
        raise RuntimeError("the ticket lookup kept failing")
    answer = think(
        run,
        model,
        1,
        f"Ticket: {json.dumps(found)}. Write a two-sentence status and a suggested reply.",
        pace,
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
    parser.add_argument(
        "--variant",
        choices=VARIANTS,
        default="recovers",
        help="what the ticket lookup does: recovers (default), clean, flaky, outage (the run fails) or denied (the run fails)",
    )
    parser.add_argument(
        "--ticket",
        choices=sorted(TICKETS),
        default=TICKET_ID,
        help="which scripted ticket to answer",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="seconds scale for pretend latencies (0 = none, the default)",
    )
    parser.add_argument(
        "--started-ago",
        type=float,
        default=None,
        metavar="HOURS",
        help="scripted model only: record the run as started this many hours ago, with believable latencies "
        "(virtual time, nothing waits; see demo_clock.py). Default: now, real time",
    )
    parser.add_argument(
        "--tokens-scale",
        type=float,
        default=1.0,
        help="scripted model only: multiply every token count (real agents send big contexts)",
    )
    parser.add_argument(
        "--model-name",
        choices=sorted(SCRIPTED_PRICES),
        default="scripted-small",
        help="scripted model only: which made-up model (and price table) the run reports",
    )
    args = parser.parse_args(argv)
    if args.live and (args.started_ago is not None or args.tokens_scale != 1.0):
        parser.error("--started-ago and --tokens-scale are for the scripted model")
    ticket = TICKETS[args.ticket]
    clock = DemoClock(hours(args.started_ago)) if args.started_ago is not None else None
    model: Model
    if args.live:
        model = AnthropicLive()
    else:
        model = ScriptedModel(ticket, tokens_scale=args.tokens_scale, name=args.model_name)
        if clock is not None:
            model = LatentModel(model, clock, lambda reply: reply.output_tokens)
    bb = BlackBox(
        project="custom-agent-demo",
        agent_id="support-agent",
        mode="offline" if args.offline else "http",
        payload_mode=PayloadMode.METADATA_ONLY,
    )
    outcome: dict[str, Any] = {"retries": 0}
    failed = False
    try:
        with bb.run(f"Answer ticket: {ticket['subject']}", metadata={"model": model.name}) as run:
            run_id = run.run_id
            outcome = run_agent(
                run, model, ticket=ticket, variant=args.variant, pace=args.pace, clock=clock
            )
    except (RuntimeError, PermissionDenied):
        failed = True  # the run is recorded as failed; the scenario is meant to end this way
    flushed = bb.shutdown(15)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "failed": failed,
                "retries": outcome["retries"],
                "flushed": flushed,
                "stats": bb.stats(),
            }
        )
    )
    if failed:
        return 1
    return 0 if (flushed or args.offline) else 1


if __name__ == "__main__":
    sys.exit(main())
