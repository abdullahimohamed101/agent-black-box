"""Optional real-model mode: the Anthropic Messages API over the standard library.

Never required and never imported in scripted mode. It needs the user's own ANTHROPIC_API_KEY, which is read
from the environment, sent only to api.anthropic.com, and (like every secret-looking environment value) masked
if it ever appears in captured output. It has not been exercised by the project's CI (KI-044).
"""

import json
import os
import urllib.request
from typing import Any

from coding_agent.models import Reply, ToolCall

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
SYSTEM = (
    "You are a careful coding agent working in a small Python repository. Use the tools to read code, "
    "make minimal edits, run the tests and, when they pass, commit on a branch and push it. "
    "Do not run destructive commands."
)


class AnthropicModel:
    provider = "anthropic"

    def __init__(self, model: str | None = None, *, timeout: float = 120.0) -> None:
        self.name = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._key:
            raise RuntimeError("real-model mode needs ANTHROPIC_API_KEY in the environment")
        self._timeout = timeout

    @staticmethod
    def _wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                wire.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if m["text"]:
                    blocks.append({"type": "text", "text": m["text"]})
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in m["tool_calls"]
                ]
                wire.append({"role": "assistant", "content": blocks})
            else:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": r["id"], "content": r["text"]}
                            for r in m["results"]
                        ],
                    }
                )
        return wire

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        body = json.dumps(
            {
                "model": self.name,
                "max_tokens": 2048,
                "system": SYSTEM,
                "tools": tools,
                "messages": self._wire(messages),
            }
        ).encode()
        request = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            data = json.loads(response.read(8 * 1024 * 1024))
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        calls = tuple(
            ToolCall(b["id"], b["name"], b.get("input") or {})
            for b in data.get("content", [])
            if b.get("type") == "tool_use"
        )
        usage = data.get("usage", {})
        return Reply(
            text, calls, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        )
