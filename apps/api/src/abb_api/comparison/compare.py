"""The comparison of two runs as a pure function of their events (ADR-092).

Everything here is computed from the projection the caller may see: relations, the divergence, the
first outcome difference and the file figures all come from the same steps, so the response is not
an equality oracle on content the caller cannot read. No database, no clock: deterministic, so two
calls with the same input are byte-identical once serialised.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.ordering import sort_events

from abb_api.comparison.align import align
from abb_api.comparison.normalise import strip_workdir
from abb_api.comparison.steps import Step, extract_steps, workdir_prefixes

SUMMARY_METRICS = (
    "duration_ms", "estimated_cost_usd", "retry_cost_usd", "llm_calls", "tool_calls",
    "retry_count", "error_count", "files_modified", "input_tokens", "output_tokens",
)  # fmt: skip
FILE_EVENTS = frozenset({"file.created", "file.modified", "file.deleted"})
MAX_FILES = 200  # paths listed per group


@dataclass(frozen=True)
class SideInput:
    run_id: str
    status: str
    started_at: datetime
    summary: Mapping[str, Any]
    events: Sequence[Event]  # at most the load cap, any order
    event_count: int  # the stored summary's figure, to tell whether `events` was cut


@dataclass(frozen=True)
class SideResult:
    run_id: str
    status: str
    steps: int
    truncated: bool
    truncated_at_event: str | None


@dataclass(frozen=True)
class Row:
    relation: str  # equal | changed | left_only | right_only
    left: Step | None
    right: Step | None


@dataclass(frozen=True)
class MetricRow:
    metric: str
    left: float | int | None
    right: float | int | None
    delta: float | int | None


@dataclass(frozen=True)
class FileDiff:
    withheld: bool
    only_left: tuple[str, ...] = ()
    only_right: tuple[str, ...] = ()
    both: tuple[str, ...] = ()
    left_count: int = 0
    right_count: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class Comparison:
    left: SideResult
    right: SideResult
    alignment: str  # content | metadata
    quality: str  # full | partial
    rows: tuple[Row, ...]
    divergence: int | None
    first_outcome_difference: int | None
    summary: tuple[MetricRow, ...]
    files: FileDiff


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _delta(left: float | int | None, right: float | int | None) -> float | int | None:
    if left is None or right is None:
        return None
    if isinstance(left, int) and isinstance(right, int):
        return right - left
    return round(float(right) - float(left), 9)


def summary_rows(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[MetricRow, ...]:
    rows = []
    for metric in SUMMARY_METRICS:
        a, b = _number(left.get(metric)), _number(right.get(metric))
        rows.append(MetricRow(metric, a, b, _delta(a, b)))
    return tuple(rows)


def _paths(events: Sequence[Event], prefixes: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for event in events:
        path = event.attributes.get("file.path")
        if event.event_type in FILE_EVENTS and isinstance(path, str) and path:
            found.add(strip_workdir(path, prefixes).lstrip("/"))
    return found


def file_diff(
    left: Sequence[Event],
    right: Sequence[Event],
    left_count: int,
    right_count: int,
    *,
    content: bool,
) -> FileDiff:
    """With `payload.read`, the modified paths of each side. Without it, per-side counts only: an
    intersection would be a set-membership oracle on withheld paths."""
    if not content:
        return FileDiff(withheld=True, left_count=left_count, right_count=right_count)
    a, b = _paths(left, workdir_prefixes(left)), _paths(right, workdir_prefixes(right))
    groups = (sorted(a - b), sorted(b - a), sorted(a & b))
    return FileDiff(
        withheld=False,
        only_left=tuple(groups[0][:MAX_FILES]),
        only_right=tuple(groups[1][:MAX_FILES]),
        both=tuple(groups[2][:MAX_FILES]),
        left_count=len(a),
        right_count=len(b),
        truncated=any(len(g) > MAX_FILES for g in groups),
    )


def classify(left: Step | None, right: Step | None, matched: bool) -> str:
    if left is None:
        return "right_only"
    if right is None:
        return "left_only"
    if matched and left.status == right.status:
        return "equal"
    return "changed"


def compare_runs(
    left: SideInput, right: SideInput, *, content: bool, max_steps: int, max_edits: int
) -> Comparison:
    ordered = [sort_events(side.events) for side in (left, right)]
    extracted = [
        extract_steps(events, started_at=side.started_at, content=content, limit=max_steps)
        for events, side in zip(ordered, (left, right), strict=True)
    ]
    (left_steps, left_cut), (right_steps, right_cut) = extracted
    alignment = align(
        [s.key for s in left_steps], [s.key for s in right_steps], max_edits=max_edits
    )
    rows: list[Row] = []
    divergence = first_outcome = None
    for index, pair in enumerate(alignment.pairs):
        a = left_steps[pair.left] if pair.left is not None else None
        b = right_steps[pair.right] if pair.right is not None else None
        row = Row(classify(a, b, pair.matched), a, b)
        rows.append(row)
        if divergence is None and row.relation != "equal":
            divergence = index
        if (
            first_outcome is None
            and pair.matched
            and a is not None
            and b is not None
            and a.status != b.status
        ):
            first_outcome = index

    def side(data: SideInput, steps: list[Step], cut: str | None, events: int) -> SideResult:
        cut_events = data.event_count > events
        last = steps[-1].event_id if steps else None
        return SideResult(
            run_id=data.run_id,
            status=data.status,
            steps=len(steps),
            truncated=cut is not None or cut_events,
            truncated_at_event=cut or (last if cut_events else None),
        )

    left_files = _number(left.summary.get("files_modified")) or 0
    right_files = _number(right.summary.get("files_modified")) or 0
    return Comparison(
        left=side(left, left_steps, left_cut, len(ordered[0])),
        right=side(right, right_steps, right_cut, len(ordered[1])),
        alignment="content" if content else "metadata",
        quality=alignment.quality,
        rows=tuple(rows),
        divergence=divergence,
        first_outcome_difference=first_outcome,
        summary=summary_rows(left.summary, right.summary),
        files=file_diff(ordered[0], ordered[1], int(left_files), int(right_files), content=content),
    )
