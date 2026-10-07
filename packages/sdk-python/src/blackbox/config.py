"""Configuration with the spec's defaults (§67.3). Invalid values fall back to defaults (INV-4)."""

import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from blackbox.redaction import DEFAULT_DENY_KEYS, Callback, PayloadMode

log = logging.getLogger("blackbox")

MODES = ("http", "local", "offline", "disabled")
DEFAULT_ENDPOINT = "http://localhost:8000"
_AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def agent_slug(value: str | None, default: str = "agent") -> str:
    """Coerce free text into the contract's agent slug (lowercase, `[a-z0-9._-]`, <= 64)."""
    text = re.sub(r"[^a-z0-9._-]+", "-", (value or "").strip().lower()).strip("-._")[:64]
    return text if _AGENT_RE.fullmatch(text) else default


def _endpoint_ok(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
        return parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:  # malformed IPv6 literal, port out of range
        return False


def _bounded(name: str, value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if not low <= number <= high:  # also rejects NaN
        log.warning(
            "blackbox: %s=%r is outside [%s, %s]; using %s", name, value, low, high, default
        )
        return default
    return number


@dataclass
class Config:
    api_key: str | None = field(default=None, repr=False)  # never show up in logs or tracebacks
    endpoint: str = DEFAULT_ENDPOINT
    mode: str = "http"
    project: str | None = None  # informational: the API key already determines the project
    agent_id: str = "agent"
    agent_version: str | None = None
    local_path: str | None = None
    # spec §67.3 defaults
    batch_size: int = 100
    flush_interval: float = 0.25
    max_queue: int = 10_000
    http_timeout: float = 2.0
    # ADR-013 retry policy
    max_attempts: int = 5
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    retry_after_max: float = 60.0
    max_batch_bytes: int = 4 * 1024 * 1024
    gzip_min_bytes: int = 1024
    shutdown_timeout: float = 5.0
    # redaction (ADR-010)
    payload_mode: PayloadMode = PayloadMode.METADATA_ONLY
    deny_keys: tuple[str, ...] = DEFAULT_DENY_KEYS
    allow_keys: tuple[str, ...] = ()
    redactor: Callback | None = None
    tags: tuple[str, ...] = ()
    wait: Callable[[float], bool] | None = field(default=None, repr=False)  # tests: backoff sleeper

    @classmethod
    def build(cls, **given: Any) -> "Config":
        """Merge arguments with BLACKBOX_* environment variables; never raises."""
        env = os.environ
        c = cls()
        c.api_key = given.get("api_key") or env.get("BLACKBOX_API_KEY") or None
        c.endpoint = str(given.get("endpoint") or env.get("BLACKBOX_ENDPOINT") or DEFAULT_ENDPOINT)
        mode = str(given.get("mode") or env.get("BLACKBOX_MODE") or "http").lower()
        if mode not in MODES:
            log.warning("blackbox: unknown mode %r; telemetry disabled", mode)
            mode = "disabled"
        c.mode = mode
        c.project = given.get("project")
        c.agent_id = agent_slug(given.get("agent_id") or c.project)
        c.agent_version = given.get("agent_version")
        c.local_path = given.get("local_path") or env.get("BLACKBOX_LOCAL_PATH")
        c.batch_size = int(_bounded("batch_size", given.get("batch_size", 100), 100, 1, 1000))
        c.flush_interval = _bounded(
            "flush_interval", given.get("flush_interval", 0.25), 0.25, 0.01, 60
        )
        c.max_queue = int(
            _bounded("max_queue", given.get("max_queue", 10_000), 10_000, 10, 10_000_000)
        )
        c.http_timeout = _bounded("http_timeout", given.get("http_timeout", 2.0), 2.0, 0.1, 60)
        c.max_attempts = int(_bounded("max_attempts", given.get("max_attempts", 5), 5, 1, 20))
        c.backoff_base = _bounded("backoff_base", given.get("backoff_base", 0.5), 0.5, 0.0, 60)
        c.backoff_max = _bounded("backoff_max", given.get("backoff_max", 30.0), 30.0, 0.0, 600)
        c.retry_after_max = _bounded(
            "retry_after_max", given.get("retry_after_max", 60.0), 60.0, 0, 3600
        )
        c.shutdown_timeout = _bounded(
            "shutdown_timeout", given.get("shutdown_timeout", 5.0), 5.0, 0, 300
        )
        c.max_batch_bytes = int(
            _bounded(
                "max_batch_bytes", given.get("max_batch_bytes", 4 << 20), 4 << 20, 1024, 4 << 20
            )
        )
        c.gzip_min_bytes = int(
            _bounded("gzip_min_bytes", given.get("gzip_min_bytes", 1024), 1024, 0, 1 << 30)
        )
        try:
            c.payload_mode = PayloadMode(given.get("payload_mode", PayloadMode.METADATA_ONLY))
        except ValueError:
            log.warning("blackbox: unknown payload_mode; using metadata_only")
        deny: Iterable[str] = given.get("deny_keys") or ()
        c.deny_keys = DEFAULT_DENY_KEYS + tuple(deny)  # additive: callers cannot weaken the default
        c.allow_keys = tuple(given.get("allow_keys") or ())
        c.redactor = given.get("redactor")
        c.tags = tuple(given.get("tags") or ())
        c.wait = given.get("wait")
        if c.mode == "http" and not _endpoint_ok(c.endpoint):
            log.warning("blackbox: endpoint is not a valid http(s) URL; telemetry disabled")
            c.mode = "disabled"
        if c.mode == "http" and not c.api_key:
            log.warning("blackbox: no API key (api_key= or BLACKBOX_API_KEY); telemetry disabled")
            c.mode = "disabled"
        if c.mode == "local" and not c.local_path:
            log.warning("blackbox: local mode needs local_path=; telemetry disabled")
            c.mode = "disabled"
        return c
