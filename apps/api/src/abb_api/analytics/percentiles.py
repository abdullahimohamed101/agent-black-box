"""Approximate percentiles from log-spaced duration histograms (ADR-043).

Rollups keep, per day and series, how many durations fell into each bucket. Buckets are
geometric (about 10% wide) from 1 ms to 1 hour, so a percentile read from merged buckets is
within about 5% of the exact value. Bucket 0 holds everything under 1 ms and the last bucket
everything from 1 hour up. The same bucket function is used in SQL (`bucket_sql`) and here, so
live and rolled-up days merge consistently.
"""

import math
from collections.abc import Mapping

from sqlalchemy import ColumnElement, func, literal

BUCKETS = 160
MIN_MS = 1.0
MAX_MS = 3_600_000.0
_LN_MAX = math.log(MAX_MS)
_WIDTH = _LN_MAX / BUCKETS
OVERFLOW = BUCKETS + 1


def bucket_sql(duration_ms: ColumnElement[float]) -> ColumnElement[int]:
    """SQL expression for the bucket of a duration (0 .. 161)."""
    return func.width_bucket(
        func.ln(func.greatest(duration_ms, 0.5)), literal(0.0), literal(_LN_MAX), BUCKETS
    )


def bucket_of(duration_ms: float) -> int:
    """Python twin of `bucket_sql` (tests assert the two agree)."""
    value = math.log(max(duration_ms, 0.5))
    if value < 0:
        return 0
    if value >= _LN_MAX:
        return OVERFLOW
    return int(value / _WIDTH) + 1


def bucket_bounds(bucket: int) -> tuple[float, float]:
    """The [low, high) range of a bucket in milliseconds."""
    if bucket <= 0:
        return 0.0, MIN_MS
    if bucket >= OVERFLOW:
        return MAX_MS, MAX_MS * 2
    return math.exp((bucket - 1) * _WIDTH), math.exp(bucket * _WIDTH)


def percentile(counts: Mapping[int, int], q: float) -> float | None:
    """The q-quantile (0..1) of the merged histogram; None if empty.

    Follows `percentile_cont`: the value at fractional rank q * (n - 1), interpolated between
    the two neighbouring ranks. A rank's value is estimated by spreading the samples of its
    bucket evenly (geometrically) across it, so small samples give sensible answers and large
    ones are within the bucket width.
    """
    total = sum(counts.values())
    if total <= 0:
        return None
    ordered = sorted(counts.items())

    def value_at(rank: int) -> float:
        seen = 0
        for bucket, n in ordered:
            if n > 0 and rank < seen + n:
                low, high = bucket_bounds(bucket)
                fraction = (rank - seen + 0.5) / n
                return (
                    high * fraction if low <= 0 else low * math.exp(math.log(high / low) * fraction)
                )
            seen += n
        return bucket_bounds(ordered[-1][0])[1]

    position = q * (total - 1)
    lower = math.floor(position)
    upper = min(lower + 1, total - 1)
    weight = position - lower
    return value_at(lower) * (1 - weight) + value_at(upper) * weight
