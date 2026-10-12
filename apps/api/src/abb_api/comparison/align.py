"""Step alignment: common prefix and suffix, then a budgeted Myers diff of the middle (ADR-092).

Pure and deterministic. The end of the common prefix is the first structural divergence for every
input, found in linear time; the middle is aligned with Myers' O(ND) algorithm, but only while the
edit distance stays within `max_edits` and the work within `WORK_CAP` comparisons. Past either, the
middle is reported as one unaligned block per side and the quality is `partial`: the answer is
bounded in time whatever the input (low-cardinality sequences are the worst case of LCS-style
matchers, which is why `difflib.SequenceMatcher` is not used).

The result does not depend on the order of the arguments: the problem is solved in a canonical
orientation (the lexicographically smaller key sequence on the left) and mirrored back.
"""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

WORK_CAP = 4_000_000  # comparisons plus diagonal steps, over the whole middle


@dataclass(frozen=True)
class Pair:
    """One row of the alignment. `left` or `right` is None for a step present on one side only.
    `matched` is True when the two keys are equal; a zipped replacement is a pair with
    `matched=False`."""

    left: int | None
    right: int | None
    matched: bool


@dataclass(frozen=True)
class Alignment:
    pairs: tuple[Pair, ...]
    quality: str  # "full" | "partial"


def _myers(
    a: list[int], b: list[int], max_d: int, work_cap: int
) -> list[tuple[str, int, int]] | None:
    """Edit script turning `a` into `b` as ('eq', i, j), ('del', i, -1), ('ins', -1, j) in order,
    or None when more than `max_d` edits (or more than `work_cap` steps) are needed."""
    n, m = len(a), len(b)
    max_d = min(max_d, n + m)
    off = max_d + 1
    v = [0] * (2 * max_d + 3)
    trace: list[list[int]] = []
    work = 0
    found = False
    for d in range(max_d + 1):
        trace.append(v[:])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[off + k - 1] < v[off + k + 1]):
                x = v[off + k + 1]
            else:
                x = v[off + k - 1] + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
                work += 1
            v[off + k] = x
            work += 1
            if x >= n and y >= m:
                found = True
                break
        if found:
            break
        if work > work_cap:
            return None
    if not found:
        return None
    ops: list[tuple[str, int, int]] = []
    x, y = n, m
    for d in range(len(trace) - 1, -1, -1):
        prev = trace[d]
        k = x - y
        if k == -d or (k != d and prev[off + k - 1] < prev[off + k + 1]):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = prev[off + prev_k]
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            x -= 1
            y -= 1
            ops.append(("eq", x, y))
        if d > 0:
            if x == prev_x:
                ops.append(("ins", -1, prev_y))
            else:
                ops.append(("del", prev_x, -1))
        x, y = prev_x, prev_y
    ops.reverse()
    return ops


def _pairs(ops: list[tuple[str, int, int]], base_l: int, base_r: int) -> list[Pair]:
    """Rows from an edit script. A run of deletions and insertions between two equal steps is a
    replacement: its steps are zipped into unmatched pairs, the remainder is one-sided."""
    rows: list[Pair] = []
    dels: list[int] = []
    inss: list[int] = []

    def flush() -> None:
        paired = min(len(dels), len(inss))
        rows.extend(Pair(dels[i], inss[i], False) for i in range(paired))
        rows.extend(Pair(i, None, False) for i in dels[paired:])
        rows.extend(Pair(None, j, False) for j in inss[paired:])
        dels.clear()
        inss.clear()

    for kind, i, j in ops:
        if kind == "eq":
            flush()
            rows.append(Pair(base_l + i, base_r + j, True))
        elif kind == "del":
            dels.append(base_l + i)
        else:
            inss.append(base_r + j)
    flush()
    return rows


def _canonical_ids(
    left: Sequence[Hashable], right: Sequence[Hashable]
) -> tuple[list[int], list[int]]:
    """Integer ids for the keys, assigned in sorted order (independent of argument order)."""
    distinct = sorted(set(left) | set(right), key=repr)
    ids = {key: n for n, key in enumerate(distinct)}
    return [ids[k] for k in left], [ids[k] for k in right]


def _solve(a: list[int], b: list[int], max_edits: int, work_cap: int) -> Alignment:
    n, m = len(a), len(b)
    prefix = 0
    while prefix < n and prefix < m and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while suffix < n - prefix and suffix < m - prefix and a[n - 1 - suffix] == b[m - 1 - suffix]:
        suffix += 1
    rows = [Pair(i, i, True) for i in range(prefix)]
    mid_a, mid_b = a[prefix : n - suffix], b[prefix : m - suffix]
    quality = "full"
    if not mid_a or not mid_b:
        rows.extend(Pair(prefix + i, None, False) for i in range(len(mid_a)))
        rows.extend(Pair(None, prefix + j, False) for j in range(len(mid_b)))
    else:
        ops = _myers(mid_a, mid_b, max_edits, work_cap)
        if ops is None:
            quality = "partial"
            rows.extend(Pair(prefix + i, None, False) for i in range(len(mid_a)))
            rows.extend(Pair(None, prefix + j, False) for j in range(len(mid_b)))
        else:
            rows.extend(_pairs(ops, prefix, prefix))
    rows.extend(Pair(n - suffix + t, m - suffix + t, True) for t in range(suffix))
    return Alignment(tuple(rows), quality)


def align(
    left: Sequence[Hashable],
    right: Sequence[Hashable],
    *,
    max_edits: int,
    work_cap: int = WORK_CAP,
) -> Alignment:
    a, b = _canonical_ids(left, right)
    if a > b:  # solve the smaller-first orientation, then mirror: align(a, b) mirrors align(b, a)
        flipped = _solve(b, a, max_edits, work_cap)
        return Alignment(
            tuple(Pair(p.right, p.left, p.matched) for p in flipped.pairs), flipped.quality
        )
    return _solve(a, b, max_edits, work_cap)
