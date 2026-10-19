#!/usr/bin/env python3
"""Pure logic behind scripts/hosting-backup.sh and scripts/hosting-restore-drill.sh (Phase 23, step 7). Standard library only.

Kept out of the shell so the rules that delete data and the rules that judge freshness can be unit-tested
(scripts/tests/test_hosting_backup.py), including the failure cases.

    hosting_backup_lib.py name [--now ISO]                         a new bundle name:  abb-20261019T023000Z
    hosting_backup_lib.py prune-plan --keep-days D --keep-min N --require NAME [--now ISO] [--grace-days G] < LISTING
    hosting_backup_lib.py metrics --file F KEY=VALUE ...           write the node_exporter textfile atomically
    hosting_backup_lib.py check --file F [--max-age-hours H]       the first-run / freshness check (KI-243)
    hosting_backup_lib.py latest-complete < LISTING                the newest COMPLETE bundle name
    hosting_backup_lib.py sha256 FILE                              hex digest (portable: no sha256sum on macOS)
    hosting_backup_lib.py manifest-counts BUNDLE_DIR               "table count" lines of a bundle's manifest

LISTING (stdin) is `rclone lsf -R` output: lines `<bundle>/COMPLETE` mark a finished bundle, any other line is a file
of a bundle. Bundle names that do not look like `abb-<UTC timestamp>` are never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

NAME = re.compile(r"^abb-(\d{8}T\d{6}Z)$")
TABLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
METRIC = re.compile(r"^abb_hosting_[a-z_]+$")
# What the freshness check reads. The first one is the success of a WHOLE cycle (created, verified, uploaded, checked).
LAST_SUCCESS = "abb_hosting_backup_last_success_timestamp_seconds"


class RuleError(Exception):
    """A safety rule refused the request. The message is safe to print."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_now(text: Optional[str]) -> datetime:
    if not text:
        return utcnow()
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def bundle_name(now: datetime) -> str:
    return "abb-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def bundle_time(name: str) -> Optional[datetime]:
    match = NAME.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def parse_listing(lines: Iterable[str]) -> Tuple[List[str], List[str]]:
    """(complete bundle names, incomplete bundle names) from `rclone lsf -R` lines. Foreign names are dropped."""
    seen: Dict[str, bool] = {}
    for raw in lines:
        line = raw.strip().strip("/")
        if not line:
            continue
        top, _, rest = line.partition("/")
        if bundle_time(top) is None:
            continue
        seen[top] = seen.get(top, False) or rest == "COMPLETE"
    complete = sorted(n for n, done in seen.items() if done)
    return complete, sorted(n for n, done in seen.items() if not done)


def prune_plan(
    listing: Iterable[str],
    *,
    now: datetime,
    keep_days: int,
    keep_min: int,
    require: str,
    grace_days: int = 2,
) -> Tuple[List[str], List[str]]:
    """(bundles to delete, bundles kept).

    The rules, each one a way a prune could destroy the only good backup:
      * the newest `keep_min` COMPLETE bundles are never deleted, whatever their age (a stalled schedule must not empty the
        remote: after a month of failures the last good bundles stay);
      * a COMPLETE bundle is deleted only when it is older than `keep_days`;
      * a bundle without COMPLETE (an interrupted upload) does not count as a backup, is never counted toward the newest N,
        and is deleted only when older than `grace_days` (a running upload is not touched);
      * `require` (the bundle this run just uploaded and checked) must be present and COMPLETE in the listing, otherwise the
        listing is not trusted (empty, truncated, wrong path) and nothing is deleted;
      * a bundle dated in the future (clock skew) is kept and counts as newest;
      * names that are not bundle names are never touched; keep_min below 1 or keep_days below 1 is refused.
    """
    if keep_min < 1:
        raise RuleError("keep-min must be at least 1")
    if keep_days < 1:
        raise RuleError("keep-days must be at least 1")
    if grace_days < 1:
        raise RuleError("grace-days must be at least 1")
    complete, incomplete = parse_listing(listing)
    if require not in complete:
        raise RuleError("the bundle this run uploaded is not complete in the remote listing: refusing to delete anything")
    newest_first = sorted(complete, reverse=True)
    protected = set(newest_first[:keep_min])
    delete: List[str] = []
    for name in newest_first:
        when = bundle_time(name)
        assert when is not None
        if name in protected or when > now:
            continue
        if now - when > timedelta(days=keep_days):
            delete.append(name)
    for name in incomplete:
        when = bundle_time(name)
        assert when is not None
        if now - when > timedelta(days=grace_days):
            delete.append(name)
    kept = [n for n in newest_first if n not in delete]
    return sorted(delete), kept


def write_metrics(path: Path, values: Dict[str, float]) -> None:
    """Replace the textfile atomically (node_exporter must never read a half-written file). Mode 0644: it holds no secret."""
    lines: List[str] = []
    for key in sorted(values):
        if not METRIC.match(key):
            raise RuleError("not an abb_hosting_* metric name: " + key)
        lines.append("# TYPE %s gauge" % key)
        value = float(values[key])
        lines.append("%s %s" % (key, "%d" % value if value == int(value) else repr(value)))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-" + path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_metrics(path: Path) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, _, value = line.partition(" ")
        try:
            values[key] = float(value)
        except ValueError:
            continue
    return values


def check_freshness(path: Path, *, max_age_hours: float, now: Optional[float] = None) -> List[Tuple[str, str]]:
    """[(status, message)] with status PASS or FAIL. The first-run check of KI-243: a backup that was never scheduled writes
    no file at all, so a missing file is a FAIL, not "nothing to report"."""
    now = time.time() if now is None else now
    if not path.exists():
        return [("FAIL", "no backup metrics file at %s: the backup has never completed (is abb-backup.timer enabled?)" % path)]
    try:
        values = read_metrics(path)
    except (OSError, ValueError):
        return [("FAIL", "the backup metrics file %s cannot be read" % path)]
    last = values.get(LAST_SUCCESS)
    if not last:
        return [("FAIL", "the metrics file records no completed backup (read the last run's output)")]
    age_h = (now - last) / 3600.0
    if age_h < -0.1:
        return [("FAIL", "the last backup is dated in the future (clock skew)")]
    if age_h > max_age_hours:
        return [("FAIL", "the last complete backup is %.1f hours old (limit %.0f)" % (age_h, max_age_hours))]
    results = [("PASS", "the last complete backup is %.1f hours old (limit %.0f)" % (age_h, max_age_hours))]
    if values.get("abb_hosting_backup_last_run_success", 1.0) == 0.0:
        results.append(("FAIL", "the most recent run failed (the previous success is still within the limit)"))
    return results


def manifest_counts(bundle: Path) -> List[Tuple[str, int]]:
    """(table, rows) pairs of a bundle's MANIFEST.json, table names validated: they end up in a SQL text."""
    data = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    tables = data.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise RuleError("the manifest lists no tables")
    out: List[Tuple[str, int]] = []
    for table, count in sorted(tables.items()):
        if not TABLE.match(table) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuleError("the manifest holds a malformed table entry")
        out.append((table, count))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("name")
    p.add_argument("--now")
    p = sub.add_parser("prune-plan")
    p.add_argument("--keep-days", type=int, required=True)
    p.add_argument("--keep-min", type=int, required=True)
    p.add_argument("--grace-days", type=int, default=2)
    p.add_argument("--require", required=True)
    p.add_argument("--now")
    p = sub.add_parser("metrics")
    p.add_argument("--file", required=True)
    p.add_argument("pairs", nargs="+")
    p = sub.add_parser("check")
    p.add_argument("--file", required=True)
    p.add_argument("--max-age-hours", type=float, default=36.0)
    sub.add_parser("latest-complete")
    p = sub.add_parser("sha256")
    p.add_argument("path")
    p = sub.add_parser("manifest-counts")
    p.add_argument("bundle")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "name":
            print(bundle_name(parse_now(args.now)))
        elif args.cmd == "prune-plan":
            delete, _kept = prune_plan(
                sys.stdin.read().splitlines(),
                now=parse_now(args.now),
                keep_days=args.keep_days,
                keep_min=args.keep_min,
                require=args.require,
                grace_days=args.grace_days,
            )
            for name in delete:
                print(name)
        elif args.cmd == "metrics":
            values: Dict[str, float] = {}
            for pair in args.pairs:
                key, _, value = pair.partition("=")
                values[key] = float(value)
            write_metrics(Path(args.file), values)
        elif args.cmd == "check":
            bad = False
            for status, message in check_freshness(Path(args.file), max_age_hours=args.max_age_hours):
                print("%s backup.fresh  %s" % (status, message))
                bad = bad or status == "FAIL"
            return 1 if bad else 0
        elif args.cmd == "latest-complete":
            complete, _ = parse_listing(sys.stdin.read().splitlines())
            if not complete:
                raise RuleError("no complete bundle in the listing")
            print(complete[-1])
        elif args.cmd == "sha256":
            print(hashlib.sha256(Path(args.path).read_bytes()).hexdigest())
        elif args.cmd == "manifest-counts":
            for table, count in manifest_counts(Path(args.bundle)):
                print(table, count)
    except RuleError as exc:
        print("hosting-backup: %s" % exc, file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print("hosting-backup: %s" % type(exc).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
