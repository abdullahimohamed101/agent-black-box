# agent-black-box-mcp

MCP client instrumentation for Agent Black Box (spec 18, 70; ADR-050..052). Runtime dependency: `agent-black-box` only;
`pip install agent-black-box-mcp[mcp]` adds the MCP SDK.

```python
from blackbox_mcp import instrument

instrument(session, bb, server="github")  # a ClientSession or mcp.Client; the only change
async with bb.run("triage"):
    await session.call_tool("search_issues", {"q": "timeout"})
```

- `call_tool` becomes `tool.call.started/completed/failed` with `tool.name`, `tool.operation=mcp.call_tool`, `mcp.server` (the label you give),
  `tool.result_count` and `framework.name=mcp`, nested under the current span. A result flagged `is_error`/`isError` is `tool.call.failed`
  (`tool.error_type=ToolResultError`) and is still returned to your code; raised exceptions (timeouts, protocol errors) are recorded and re-raised unchanged.
- Outside `bb.run(...)` a call is a transparent pass-through. Wrap either the high-level `Client` or its `session`, not both.
- Arguments and results are not recorded; `capture_payloads=True` attaches bounded previews, gated by `payload_mode` and redacted.
- Not wrapped: `list_tools`, resources, prompts, sampling (KI-062).
