"""The model interface and the scripted model used for deterministic runs and the E2E test."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class Model(Protocol):
    provider: str
    name: str

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply: ...


# Illustrative prices (USD per token) for the scripted model's cost estimate. Not a real price list.
_IN, _OUT = 3e-6, 15e-6


def _reply(turn: int, text: str, *calls: tuple[str, dict[str, Any]]) -> Reply:
    tokens_in, tokens_out = 900 + 650 * turn, 60 + 25 * len(calls) + len(text) // 4
    return Reply(
        text,
        tuple(ToolCall(f"call_{turn}_{i}", n, a) for i, (n, a) in enumerate(calls)),
        tokens_in,
        tokens_out,
        round(tokens_in * _IN + tokens_out * _OUT, 6),
    )


def _script() -> list[Reply]:
    first_fix = ("app/session.py", "    return now > session.expires_at",
                 "    return now >= session.expires_at - REFRESH_SKEW_SECONDS")  # fmt: skip
    second_fix = ("app/session.py", "        token.refresh_token,\n",
                  "        token.refresh_token or session.refresh_token,\n")  # fmt: skip
    return [
        _reply(
            0,
            "Users are logged out early. I'll read the session code and its tests first.",
            ("read_file", {"path": "app/session.py"}),
            ("read_file", {"path": "tests/test_session.py"}),
        ),
        _reply(
            1,
            "Let me find every place that decides whether a session has expired.",
            ("search", {"pattern": r"expires_at|is_expired"}),
        ),
        _reply(
            2,
            "is_expired ignores the refresh skew window, so a token that dies in flight is "
            "still used. I'll refresh early.",
            ("edit_file", dict(zip(("path", "old", "new"), first_fix, strict=True))),
        ),
        _reply(3, "Running the tests.", ("run_tests", {})),
        _reply(
            4,
            "One test still fails: the refresh token is gone after a refresh. I'll check the "
            "auth server log for what the provider returned.",
            ("run_command", {"command": "tail -n 3300 logs/auth-server.log"}),
            ("read_file", {"path": "app/oauth.py"}),
        ),
        _reply(
            5,
            "The provider omits refresh_token on refresh, and refresh_session copies None "
            "over it. Keep the old token when no new one is issued.",
            ("edit_file", dict(zip(("path", "old", "new"), second_fix, strict=True))),
        ),
        _reply(6, "Running the tests again.", ("run_tests", {})),
        _reply(
            7,
            "All green. Reviewing the diff and committing on a branch.",
            ("git_diff", {}),
            ("git_branch", {"name": "fix/oauth-session-expiry"}),
            (
                "git_commit",
                {"message": "Refresh sessions before expiry and keep the refresh token"},
            ),
            ("git_push", {"branch": "fix/oauth-session-expiry"}),
        ),
        _reply(
            8,
            "Fixed: sessions refresh inside the 60 s skew window and a refresh without a new "
            "refresh token keeps the old one. All six tests pass; pushed fix/oauth-session-expiry.",
        ),
    ]


@dataclass
class ScriptedModel:
    """Plays a fixed plan, one turn per `complete` call. Deterministic: no network, no randomness."""

    provider: str = "scripted"
    name: str = "scripted-agent-1"
    turns: list[Reply] = field(default_factory=_script)
    _next: int = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        if self._next >= len(self.turns):
            return Reply("(script finished)")
        reply = self.turns[self._next]
        self._next += 1
        return reply
