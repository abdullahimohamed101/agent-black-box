"""Agent Black Box Python SDK: trace an agent in a few lines, without ever harming it.

bb = BlackBox(api_key="abb_live_...", project="demo")
with bb.run("fix bug") as run:
    with run.span("search", kind="tool") as span:
        span.set_attribute("tool.result_count", 3)
bb.shutdown()
"""

from blackbox.client import AgentSpan, BlackBox, LlmCall, Run, Span
from blackbox.context import bind, current_run, current_span
from blackbox.propagation import (
    TRACE_CONTEXT_ENV,
    TRACE_CONTEXT_HEADER,
    TraceContext,
    trace_context_from_env,
)
from blackbox.redaction import PayloadMode
from blackbox.version import __version__

__all__ = [
    "TRACE_CONTEXT_ENV",
    "TRACE_CONTEXT_HEADER",
    "AgentSpan",
    "BlackBox",
    "LlmCall",
    "PayloadMode",
    "Run",
    "Span",
    "TraceContext",
    "__version__",
    "bind",
    "current_run",
    "current_span",
    "trace_context_from_env",
]
