"""A fake Anthropic client with the same method layout as the real one (no framework needed)."""

from types import SimpleNamespace
from typing import Any

from abb_conformance import LLM_CACHED, LLM_INPUT, LLM_OUTPUT


def chat_usage() -> Any:
    """Anthropic usage: uncached input apart from cache reads (100 + 20 = the 120 prompt tokens)."""
    return SimpleNamespace(
        input_tokens=LLM_INPUT - LLM_CACHED,
        output_tokens=LLM_OUTPUT,
        cache_read_input_tokens=LLM_CACHED,
        cache_creation_input_tokens=0,
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


def fake_client(chat: Any = None, *, error: BaseException | None = None, aio: bool = False) -> Any:
    cls = FakeAsyncResource if aio else FakeResource
    result = chat if chat is not None else SimpleNamespace(usage=chat_usage())
    return SimpleNamespace(messages=cls(result, error))
