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

## Policy (Phase 14)

With `policy_mode="enforce"` (or `BLACKBOX_POLICY=enforce`) on the client, `call_tool` asks the policy service before each call inside a run. Needs `agent-black-box>=0.4.0` and a project
key with `policy:check` or `events:write`.

- Capability `tool.<name>` (characters other than letters, digits, `_`, `.`, `-` become `_`), resource `mcp.call_tool` (the `tool.operation` this adapter records, which is what a dry run
  reads), no arguments. A policy rule is therefore written `capability: tool.delete_all`.
- **Denied** (or the service cannot be asked under a closed fail mode): the call is **not made**, no `tool.call.*` event is recorded (the server records `policy.action.blocked`), and the
  caller gets an MCP error result (`isError=True`, one text block, "Blocked by policy (rule vN): ...") - the shape a failing tool returns, so hosts and models handle it as usual. Nothing is raised.
- **Needs approval**: the call waits (the SDK's bounded long poll) and then proceeds or is blocked. A host that must not block can run the call in a task.
- Unclassified tool calls fail open when the service is down; set `policy_fail_modes={"tool.*": "closed"}` for the opposite.
- Cooperative: only calls through the instrumented session are asked. Outside `bb.run(...)` there is nothing to bind a decision to, so the call passes through. A tool name that cannot make a
  valid capability (over 64 characters in one segment, for instance) is not sent; the fail mode applies.
