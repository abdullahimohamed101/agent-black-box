"""Metrics for short-lived processes, as a Prometheus textfile (Phase 19c step 2, ADR-166).

`backup`, `rotate` and the retention runner are not scraped: a backup lives for minutes and the
others have no HTTP port. They write a small file that node_exporter's textfile collector exposes
(``--collector.textfile.directory``). The rules:

- Only metrics from the closed catalogue and only the names the tool owns, rendered by the same
  registry as `/metrics`, so the file parses with the same parser and carries no label the
  catalogue forbids. No secret, no path, no URL, no tenant text can get in: there is no free text.
- Atomic: the file is written to a temp name in the same directory, flushed and fsynced, given mode
  0644 (the collector runs as another user) and renamed over the target. A reader sees the old
  file or the new one, never a half-written one.
- Counters and gauges survive across invocations by being seeded from the file the previous
  invocation wrote (`seed`), so a failed backup does not erase the last success timestamp and
  counters keep growing. A missing or damaged file just starts from zero.
- A failure to write is logged by class name and never changes the tool's exit code or work.
"""

import contextlib
import logging
import math
import os
import re
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Final

from abb_api.obs.catalogue import Observability, build_observability
from abb_api.obs.metrics import Counter, Gauge

logger = logging.getLogger(__name__)

ENV_VAR: Final = "ABB_METRICS_TEXTFILE"
MAX_FILE_BYTES: Final = 256 * 1024  # a tool's own file is a few KiB; refuse to seed from more

BACKUP_METRICS: Final = frozenset(
    {
        "backup_last_success_timestamp_seconds",
        "backup_last_duration_seconds",
        "backup_bundle_bytes",
        "backup_runs_total",
    }
)
ROTATION_METRICS: Final = frozenset(
    {"rotation_last_success_timestamp_seconds", "rotation_runs_total"}
)
RETENTION_METRICS: Final = frozenset(
    {
        "retention_passes_total",
        "retention_deleted_total",
        "retention_failures_total",
        "retention_dry_run_candidates",
        "retention_mode",
    }
)

_SAMPLE = re.compile(r"^([a-z][a-z0-9_]*)(?:\{(.*)\})?\s+(\S+)$")
_LABEL = re.compile(r'([a-z_][a-z0-9_]*)="((?:[^"\\]|\\.)*)"')


def textfile_path(
    explicit: str | os.PathLike[str] | None, environ: Mapping[str, str]
) -> Path | None:
    """``--metrics-textfile`` wins over ``ABB_METRICS_TEXTFILE``; blank means off."""
    chosen = str(explicit) if explicit else environ.get(ENV_VAR, "")
    return Path(chosen) if chosen.strip() else None


def write_atomic(path: Path, body: str) -> None:
    """Write ``body`` to ``path`` so that a reader never sees a partial file."""
    directory = path.parent
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def seed(observability: Observability, path: Path, names: Collection[str]) -> None:
    """Load the previous invocation's values for ``names`` into a fresh registry (best effort)."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    metrics = observability.registry.metrics
    for line in content.splitlines():
        match = _SAMPLE.match(line)
        if match is None:
            continue
        name, raw_labels, raw_value = match.groups()
        metric = metrics.get(name)
        if metric is None or name not in names or not isinstance(metric, (Counter, Gauge)):
            continue
        found = dict(_LABEL.findall(raw_labels or ""))
        if set(found) != set(metric.label_names):
            continue
        try:
            number = float(raw_value)
        except ValueError:
            continue
        if math.isinf(number) or (math.isnan(number) and isinstance(metric, Counter)):
            continue
        values = tuple(found[n] for n in metric.label_names)
        if isinstance(metric, Counter):
            if number > 0:
                metric.inc(*values, amount=number)
        else:
            metric.set(number, *values)


class TextfileMetrics:
    """A registry for one short-lived tool, seeded from and written to its textfile.

    ``path`` None makes every method a no-op, so call sites need no branching.
    """

    def __init__(self, path: Path | None, names: Collection[str]) -> None:
        self.observability = build_observability()
        self.catalogue = self.observability.catalogue
        self._path = path
        self._names = frozenset(names)
        if path is not None:
            seed(self.observability, path, self._names)

    @property
    def enabled(self) -> bool:
        return self._path is not None

    def write(self) -> bool:
        """Persist the tool's metrics. Returns False (never raises) when it could not."""
        if self._path is None:
            return True
        try:
            write_atomic(self._path, self.observability.registry.render(self._names))
        except Exception as exc:  # a metrics file must never fail a backup
            logger.warning(
                "metrics textfile not written", extra={"error_class": type(exc).__name__}
            )
            return False
        return True
