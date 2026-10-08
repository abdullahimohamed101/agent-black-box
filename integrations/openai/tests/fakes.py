"""A fake OpenAI client with the same method layout as the real one (no framework needed)."""

from types import SimpleNamespace
from typing import Any

from abb_conformance import LLM_CACHED, LLM_INPUT, LLM_OUTPUT


def chat_usage() -> Any:
    return SimpleNamespace(
        prompt_tokens=LLM_INPUT,
        completion_tokens=LLM_OUTPUT,
        total_tokens=LLM_INPUT + LLM_OUTPUT,
        prompt_tokens_details=SimpleNamespace(cached_tokens=LLM_CACHED),
    )


def responses_usage() -> Any:
    return SimpleNamespace(
        input_tokens=LLM_INPUT,
        output_tokens=LLM_OUTPUT,
        input_tokens_details=SimpleNamespace(cached_tokens=LLM_CACHED),
    )


class FakeStream:
    def __init__(self, chunks: list[Any], fail_at: int | None = None) -> None:
        self.chunks, self.fail_at, self.closed = chunks, fail_at, False

    def __iter__(self) -> Any:
        for i, chunk in enumerate(self.chunks):
            if self.fail_at == i:
                raise ConnectionError("stream dropped")
            yield chunk

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *a: Any) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


class FakeAsyncStream(FakeStream):
    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for i, chunk in enumerate(self.chunks):
                if self.fail_at == i:
                    raise ConnectionError("stream dropped")
                yield chunk

        return gen()

    async def close(self) -> None:  # type: ignore[override]
        self.closed = True


class FakeResource:
    """`create(**kwargs)` returns `result` (or raises `error`); records calls."""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result, self.error, self.calls = result, error, []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeAsyncResource(FakeResource):
    async def create(self, **kwargs: Any) -> Any:  # type: ignore[override]
        return super().create(**kwargs)


def fake_client(
    chat: Any = None,
    responses: Any = None,
    *,
    error: BaseException | None = None,
    aio: bool = False,
) -> Any:
    cls = FakeAsyncResource if aio else FakeResource
    chat_result = chat if chat is not None else SimpleNamespace(usage=chat_usage())
    resp_result = responses if responses is not None else SimpleNamespace(usage=responses_usage())
    return SimpleNamespace(
        chat=SimpleNamespace(completions=cls(chat_result, error)), responses=cls(resp_result, error)
    )
