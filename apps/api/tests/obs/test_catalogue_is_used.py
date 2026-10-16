"""A metric declared in the catalogue must be moved by real code (so placeholders cannot rot).

The check is static: a family counts as instrumented when some module outside the catalogue reads
it as an attribute (``catalogue.worker_jobs_total``). That a family really *moves* under the right
traffic is what the sibling tests assert; this one stops a declared-but-forgotten family from
sitting in /metrics at zero forever. A family that is deliberately not incremented yet goes in the
allow-list with the reason, and the allow-list is checked too: an entry that has since been
instrumented must be removed.
"""

import re
from pathlib import Path

from abb_api.obs.catalogue import build_observability

SRC = Path(__file__).resolve().parents[2] / "src" / "abb_api"
CATALOGUE_FILE = SRC / "obs" / "catalogue.py"

# name -> why no module increments it.
NOT_INCREMENTED: dict[str, str] = {
    "process_start_time_seconds": "set once when the catalogue is built (build_catalogue)",
    "process_uptime_seconds": "a scrape-time function gauge installed by build_catalogue",
    "abb_build_info": "set once when the catalogue is built (build_catalogue)",
}


def sources() -> dict[Path, str]:
    return {
        path: path.read_text()
        for path in SRC.rglob("*.py")
        if path != CATALOGUE_FILE and "__pycache__" not in path.parts
    }


def users_of(name: str, code: dict[Path, str]) -> list[str]:
    pattern = re.compile(rf"\.{re.escape(name)}\b")
    return sorted(str(p.relative_to(SRC)) for p, text in code.items() if pattern.search(text))


def test_every_declared_family_is_incremented_somewhere_or_explicitly_excused() -> None:
    code = sources()
    unused = [
        name
        for name in build_observability().registry.metrics
        if name not in NOT_INCREMENTED and not users_of(name, code)
    ]
    assert not unused, (
        f"declared in obs/catalogue.py but never used by any module: {unused}. Instrument "
        "them, or add them to NOT_INCREMENTED with the reason."
    )


def test_the_allow_list_has_no_stale_entries_and_every_entry_has_a_reason() -> None:
    code = sources()
    declared = set(build_observability().registry.metrics)
    for name, reason in NOT_INCREMENTED.items():
        assert name in declared, f"{name} is no longer in the catalogue: drop it from the list"
        assert len(reason) > 15, f"{name}: say why it is not incremented"
        assert not users_of(name, code), f"{name} is instrumented now: drop it from the list"


def test_the_matcher_reports_a_family_nobody_reads() -> None:
    code = {SRC / "a.py": "cat.worker_jobs_total.inc(kind, outcome)\nx = worker_job_lag_seconds"}
    assert users_of("worker_jobs_total", code) == ["a.py"]
    assert not users_of("worker_job_lag_seconds", code), "a bare word is not an attribute read"
    assert not users_of("worker_jobs", code), "and a prefix is not the family"
