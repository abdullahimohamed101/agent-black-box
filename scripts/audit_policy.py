#!/usr/bin/env python3
"""Judge pip-audit JSON reports against the reviewed allow-list (stdlib only).

usage: audit_policy.py ALLOWLIST LABEL=REPORT.json [...]

Policy: any vulnerability fails the job unless an allow-list entry names its advisory id (or an
alias) and the package, and has not expired. Fixable findings should be fixed by bumping, so an
entry for one needs a reason that says why not. Expired entries fail. Entries that match nothing
are reported as stale so they get removed. Allow-list line format (see the file header):

    ADVISORY-ID | package | YYYY-MM-DD expiry | reason
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_EXPIRY_DAYS = 180


@dataclass(frozen=True)
class Entry:
    advisory: str
    package: str
    expires: dt.date
    reason: str


def parse_allowlist(text: str, today: dt.date) -> tuple[list[Entry], list[str]]:
    entries: list[Entry] = []
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 3)]
        if len(parts) != 4 or not all(parts):
            errors.append(
                f"allow-list line {number}: expected 'ID | package | YYYY-MM-DD | reason'"
            )
            continue
        try:
            expires = dt.date.fromisoformat(parts[2])
        except ValueError:
            errors.append(f"allow-list line {number}: bad expiry date {parts[2]!r}")
            continue
        if expires > today + dt.timedelta(days=MAX_EXPIRY_DAYS):
            errors.append(f"allow-list line {number}: expiry more than {MAX_EXPIRY_DAYS} days away")
            continue
        entries.append(Entry(parts[0].upper(), parts[1].lower(), expires, parts[3]))
    return entries, errors


def judge(
    allowlist: str, reports: dict[str, dict], today: dt.date | None = None
) -> tuple[list[str], list[str]]:
    """Return (failures, notices)."""
    today = today or dt.date.today()
    entries, failures = parse_allowlist(allowlist, today)
    notices: list[str] = []
    used: set[Entry] = set()
    for label, report in reports.items():
        for dependency in report.get("dependencies", []):
            name = str(dependency["name"]).lower()
            if dependency.get("skip_reason"):
                failures.append(f"{label}: {name} was not audited ({dependency['skip_reason']})")
            for vuln in dependency.get("vulns", []):
                ids = {str(vuln["id"]).upper(), *(str(a).upper() for a in vuln.get("aliases", []))}
                fixes = vuln.get("fix_versions") or []
                what = f"{label}: {name} {dependency['version']} {vuln['id']}"
                match = next((e for e in entries if e.package == name and e.advisory in ids), None)
                if match is None:
                    hint = f"fix available: {', '.join(fixes)}" if fixes else "no fix released yet"
                    failures.append(f"{what} ({hint})")
                elif match.expires < today:
                    failures.append(f"{what}: allow-list entry expired {match.expires}")
                else:
                    used.add(match)
                    notices.append(f"{what}: allowed until {match.expires} ({match.reason})")
    for entry in entries:
        if entry not in used:
            notices.append(
                f"stale allow-list entry (matches nothing, remove it): "
                f"{entry.advisory} {entry.package}"
            )
    return failures, notices


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    allowlist = Path(argv[0]).read_text(encoding="utf-8")
    reports: dict[str, dict] = {}
    for item in argv[1:]:
        label, _, path = item.partition("=")
        reports[label] = json.loads(Path(path).read_text(encoding="utf-8"))
    failures, notices = judge(allowlist, reports)
    audited = sum(len(r.get("dependencies", [])) for r in reports.values())
    for notice in notices:
        print(f"note: {notice}")
    for failure in failures:
        print(f"FAIL: {failure}")
    verdict = "FAILED" if failures else "OK"
    print(
        f"lockfile audit {verdict}: {len(reports)} lockfiles, {audited} pinned packages, "
        f"{len(failures)} failures, {len(notices)} notes"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
