"""The settings a running job handler sees.

Handlers have the signature `(job, conn)`; the worker that runs them sets its own settings here for
the duration of a job, so a handler reads the same configuration as the worker (and tests that
construct a worker with their own settings get theirs). Outside a worker (a direct call) the
process settings apply.
"""

from contextvars import ContextVar

from abb_api.core.config import Settings, get_settings
from abb_api.obs.catalogue import METRICS, Catalogue

_current: ContextVar[Settings | None] = ContextVar("job_settings", default=None)
_metrics: ContextVar[Catalogue | None] = ContextVar("job_metrics", default=None)


def job_settings() -> Settings:
    return _current.get() or get_settings()


def set_job_settings(settings: Settings) -> object:
    """Returns a token for `reset_job_settings`."""
    return _current.set(settings)


def reset_job_settings(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]


def job_metrics() -> Catalogue:
    """The metrics a running handler increments (the worker's own, else the process-wide ones)."""
    return _metrics.get() or METRICS


def set_job_metrics(catalogue: Catalogue) -> object:
    """Returns a token for `reset_job_metrics`."""
    return _metrics.set(catalogue)


def reset_job_metrics(token: object) -> None:
    _metrics.reset(token)  # type: ignore[arg-type]
