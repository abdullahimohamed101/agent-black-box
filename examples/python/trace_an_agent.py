"""Trace a small agent run end to end. Needs BLACKBOX_API_KEY (and BLACKBOX_ENDPOINT if not local).

    cd packages/sdk-python && BLACKBOX_API_KEY=... uv run python ../../examples/python/trace_an_agent.py
"""

import os

from blackbox import BlackBox

bb = BlackBox(api_key=os.environ["BLACKBOX_API_KEY"], endpoint=os.environ.get("BLACKBOX_ENDPOINT"))
with bb.run("Fix OAuth timeout", metadata={"issue": 1842}) as run:
    with run.span("github-search", kind="tool") as span:
        span.set_attribute("tool.result_count", 3)
    with run.llm_call("openai", "gpt-4.1") as llm:
        llm.record_usage(1200, 300)
bb.shutdown()
print(f"run {run.run_id}")  # noqa: T201
