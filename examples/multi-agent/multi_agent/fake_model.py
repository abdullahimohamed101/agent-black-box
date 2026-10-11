"""A deterministic stand-in for a model: fixed token counts per agent, a short pause, no network."""

import time
from dataclasses import dataclass

# Illustrative prices (USD per token), not a real price list.
_IN, _OUT = 3e-6, 15e-6

# agent -> (input tokens, output tokens, seconds of pretend latency)
_PROFILE = {
    "planner": (1800, 220, 0.30),
    "researcher": (2600, 380, 0.45),
    "coder": (3400, 640, 0.40),
    "reviewer": (2900, 260, 0.35),
}


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class FakeModel:
    provider = "demo"
    name = "model-x"

    def __init__(self, pace: float = 1.0) -> None:
        self.pace = pace  # multiplies every pause; tests use 0

    def pause(self, seconds: float) -> None:
        if self.pace > 0:
            time.sleep(seconds * self.pace)

    def complete(self, agent: str, prompt: str) -> Reply:
        tokens_in, tokens_out, latency = _PROFILE[agent]
        self.pause(latency)
        return Reply(
            f"{agent}: done ({prompt})",
            tokens_in,
            tokens_out,
            round(tokens_in * _IN + tokens_out * _OUT, 6),
        )
