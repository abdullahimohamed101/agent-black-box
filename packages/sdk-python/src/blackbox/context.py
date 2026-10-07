"""Context propagation with contextvars (spec §67.2).

The current run and span follow the code across `await` and into asyncio tasks automatically.
Threads do not inherit context: wrap the callable with `bind` when you hand work to a thread.
"""

import contextvars
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from blackbox.client import Run, Span

T = TypeVar("T")

_current_run: contextvars.ContextVar["Run | None"] = contextvars.ContextVar(
    "blackbox_run", default=None
)
_current_span: contextvars.ContextVar["Span | None"] = contextvars.ContextVar(
    "blackbox_span", default=None
)


def current_run() -> "Run | None":
    return _current_run.get()


def current_span() -> "Span | None":
    return _current_span.get()


def bind(fn: Callable[..., T]) -> Callable[..., T]:
    """Capture the caller's context now and run `fn` inside it later (e.g. in a thread pool)."""
    ctx = contextvars.copy_context()

    def bound(*args: Any, **kwargs: Any) -> T:
        return ctx.run(fn, *args, **kwargs)

    return bound
