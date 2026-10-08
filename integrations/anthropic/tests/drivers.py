"""Conformance drivers for the OpenAI wrapper: fake client and the real `openai` package."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import blackbox
from abb_conformance import SECRET, ScenarioError
from abb_conformance.scenarios import CONCURRENCY, TOOL_NAME
from blackbox import BlackBox

from blackbox_anthropic import instrument
from tests.fakes import FakeStream, fake_client

SUPPORTED = frozenset(
    "llm llm_failure nested concurrent sensitive sensitive_full hostile redactor_raises "
    "late_end".split()
)
MESSAGE_JSON = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "m1",
    "content": [{"type": "text", "text": "hi"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 30,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 0,
    },
}


def real_available() -> bool:
    try:
        import anthropic  # noqa: F401
        import httpx2  # noqa: F401
    except ImportError:
        return False
    return True


def real_client(handler: Any = None, *, aio: bool = False) -> Any:
    import anthropic
    import httpx2 as httpx

    def default(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=MESSAGE_JSON)

    transport = httpx.MockTransport(handler or default)
    if aio:
        return anthropic.AsyncAnthropic(
            api_key="test-key",
            base_url="http://mock.invalid/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        )
    return anthropic.Anthropic(
        api_key="test-key",
        base_url="http://mock.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=transport),
    )


class Driver:
    llm_provider, llm_model = "anthropic", "m1"

    def __init__(self, real: bool) -> None:
        self.real = real
        self.name = "openai-real" if real else "openai-fake"
        self.supports = SUPPORTED - ({"hostile", "llm_failure", "late_end"} if real else set())
        self.concurrent_kind = "llm"

    def client(self, scenario: str, bb: BlackBox) -> Any:
        capture = scenario == "sensitive_full"
        if self.real:
            return instrument(real_client(), bb, capture_payloads=capture)
        error = ScenarioError("boom") if scenario == "llm_failure" else None
        if scenario == "late_end":
            chunks = [SimpleNamespace(usage=None), SimpleNamespace(usage=None)]
            return instrument(fake_client(chat=FakeStream(chunks)), bb)
        return instrument(fake_client(error=error), bb, capture_payloads=capture)

    def perform(self, scenario: str, bb: BlackBox) -> None:
        client = self.client(scenario, bb)

        def call(content: Any = "hello", **extra: Any) -> None:
            client.messages.create(
                model="m1", max_tokens=16, messages=[{"role": "user", "content": content}], **extra
            )

        if scenario == "late_end":  # the caller abandons a stream after its run has ended
            with bb.run("conformance"):
                stream = client.messages.create(model="m1", max_tokens=1, messages=[], stream=True)
                next(iter(stream))
            stream.close()
            return
        with bb.run("conformance") as run:
            if scenario == "nested":
                with run.span("step", kind="agent"):
                    with run.span(TOOL_NAME, kind="tool"):
                        pass
                    call()
            elif scenario == "concurrent":
                with ThreadPoolExecutor(CONCURRENCY) as pool:
                    futures = [pool.submit(blackbox.bind(call)) for _ in range(CONCURRENCY)]
                    for future in futures:
                        future.result()
            elif scenario in ("sensitive", "sensitive_full"):
                call(SECRET)
            elif scenario == "hostile":
                weird: Any = object()
                client.messages.create(model=None, messages=weird, temperature="hot")
                client.messages.create(model=123, max_tokens=-5, temperature=-1.0)
                client.messages.create()
                client.messages.result = weird  # unusable response objects
                call()
                client.messages.result = None
                call()
            else:
                call()
