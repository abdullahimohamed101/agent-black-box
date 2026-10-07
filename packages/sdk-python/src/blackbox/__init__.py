"""Agent Black Box Python SDK: trace an agent in a few lines, without ever harming it.

bb = BlackBox(api_key="abb_live_...", project="demo")
with bb.run("fix bug") as run:
    with run.span("search", kind="tool") as span:
        span.set_attribute("tool.result_count", 3)
bb.shutdown()
"""

from blackbox.client import BlackBox, LlmCall, Run, Span
from blackbox.context import bind, current_run, current_span
from blackbox.redaction import PayloadMode
from blackbox.version import __version__

__all__ = [
    "BlackBox",
    "LlmCall",
    "PayloadMode",
    "Run",
    "Span",
    "__version__",
    "bind",
    "current_run",
    "current_span",
]
