"""Conformance drivers for the OpenAI wrapper: fake client and the real `openai` package."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import blackbox
from abb_conformance import SECRET, ScenarioError
from abb_conformance.scenarios import CONCURRENCY, TOOL_NAME
from blackbox import BlackBox

from blackbox_openai import instrument
from tests.fakes import fake_client

SUPPORTED = frozenset(
    "llm llm_failure nested concurrent sensitive sensitive_full hostile redactor_raises".split()
)
CHAT_JSON = {
    "id": "c1",
    "object": "chat.completion",
    "created": 1,
    "model": "m1",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}
    ],
    "usage": {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "prompt_tokens_details": {"cached_tokens": 20},
    },
}


def real_available() -> bool:
    try:
        import httpx  # noqa: F401
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def real_client(handler: Any = None, *, aio: bool = False) -> Any:
    import httpx
    import openai

    def default(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CHAT_JSON)

    transport = httpx.MockTransport(handler or default)
    if aio:
        return openai.AsyncOpenAI(
            api_key="test-key",
            base_url="http://mock.invalid/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        )
    return openai.OpenAI(
        api_key="test-key",
        base_url="http://mock.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=transport),
    )


class Driver:
    llm_provider, llm_model = "openai", "m1"

    def __init__(self, real: bool) -> None:
        self.real = real
        self.name = "openai-real" if real else "openai-fake"
        self.supports = SUPPORTED - ({"hostile", "llm_failure"} if real else set())
        self.concurrent_kind = "llm"

    def client(self, scenario: str, bb: BlackBox) -> Any:
        capture = scenario == "sensitive_full"
        if self.real:
            return instrument(real_client(), bb, capture_payloads=capture)
        error = ScenarioError("boom") if scenario == "llm_failure" else None
        return instrument(fake_client(error=error), bb, capture_payloads=capture)

    def perform(self, scenario: str, bb: BlackBox) -> None:
        client = self.client(scenario, bb)

        def call(content: Any = "hello", **extra: Any) -> None:
            client.chat.completions.create(
                model="m1", messages=[{"role": "user", "content": content}], **extra
            )

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
                client.chat.completions.create(model=None, messages=weird, temperature="hot")
                client.chat.completions.create(model=123, max_tokens=-5, temperature=-1.0)
                client.chat.completions.create()
                client.chat.completions.result = weird  # unusable response objects
                call()
                client.chat.completions.result = None
                call()
            else:
                call()
