"""Self-test of scripts/hosting_backup_lib.py (Phase 23, step 7): the rules that delete data and judge freshness.

Every rule is shown to bite: each test plants the situation the rule exists for and asserts the dangerous outcome is refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import hosting_backup_lib as lib  # noqa: E402

NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)


def listing(*days_ago, complete=True, hours=0):
    """rclone lsf -R lines for bundles made `days_ago` days before NOW."""
    lines = []
    for d in days_ago:
        name = lib.bundle_name(NOW - timedelta(days=d, hours=hours))
        lines += [name + "/MANIFEST.json", name + "/database.dump.enc"]
        if complete:
            lines.append(name + "/COMPLETE")
    return lines


def name_of(days_ago, hours=0):
    return lib.bundle_name(NOW - timedelta(days=days_ago, hours=hours))


class PruneTests(unittest.TestCase):
    def plan(self, lines, *, keep_days=30, keep_min=7, require=None, grace=2):
        return lib.prune_plan(lines, now=NOW, keep_days=keep_days, keep_min=keep_min, require=require or name_of(0), grace_days=grace)

    def test_old_bundles_beyond_the_minimum_are_deleted(self):
        lines = listing(*range(0, 45))
        delete, kept = self.plan(lines)
        self.assertEqual(len(kept), 31)  # days 0..30 (a bundle exactly 30 days old is not older than 30 days)
        self.assertTrue(all(lib.bundle_time(n) < NOW - timedelta(days=30) for n in delete))
        self.assertEqual(len(delete), 14)

    def test_the_newest_n_are_never_deleted_even_when_all_are_old(self):
        # The schedule stalled for 100 days: everything is older than keep-days. The newest 7 must survive.
        lines = listing(100, 101, 102, 103, 104, 105, 106, 107, 108, 109)
        delete, kept = self.plan(lines, require=name_of(100))
        self.assertEqual(sorted(kept), sorted(name_of(d) for d in (100, 101, 102, 103, 104, 105, 106)))
        self.assertEqual(len(delete), 3)
        self.assertNotIn(name_of(100), delete)

    def test_fewer_bundles_than_the_minimum_deletes_nothing(self):
        delete, kept = self.plan(listing(0, 40, 50), keep_min=7)
        self.assertEqual(delete, [])
        self.assertEqual(len(kept), 3)

    def test_an_untrusted_listing_deletes_nothing(self):
        # The upload just made is absent from the listing (wrong path, truncated listing, empty remote): no deletion at all.
        with self.assertRaises(lib.RuleError):
            self.plan(listing(40, 50, 60), require=name_of(0))
        with self.assertRaises(lib.RuleError):
            self.plan([], require=name_of(0))

    def test_the_required_bundle_must_be_complete(self):
        lines = listing(40, 50) + listing(0, complete=False)
        with self.assertRaises(lib.RuleError):
            self.plan(lines)

    def test_an_interrupted_upload_does_not_count_as_a_backup(self):
        # Seven recent PARTIAL bundles and one complete old one: the complete one is a protected "newest" bundle.
        lines = listing(0) + listing(1, 2, 3, 4, 5, 6, 7, complete=False) + listing(60)
        delete, kept = self.plan(lines, keep_min=2)
        self.assertIn(name_of(60), kept)  # newest 2 complete: day 0 and day 60
        self.assertNotIn(name_of(60), delete)

    def test_stale_partial_bundles_are_cleaned_but_a_fresh_one_is_left_alone(self):
        lines = listing(0) + listing(5, complete=False) + listing(0, hours=3, complete=False)
        delete, _ = self.plan(lines)
        self.assertEqual(delete, [name_of(5)])

    def test_future_dated_bundles_are_kept(self):
        lines = listing(0, 40) + listing(-5)
        delete, kept = self.plan(lines, keep_min=1)
        self.assertNotIn(name_of(-5), delete)
        self.assertIn(name_of(40), delete)  # kept protects only the single newest, which is the future one

    def test_foreign_names_are_never_touched(self):
        lines = listing(0, 90) + ["notes.txt", "photos/2020/a.jpg", "abb-latest/COMPLETE", "abb-20261019/COMPLETE", "abb-20200101T000000Z.tar"]
        complete, incomplete = lib.parse_listing(lines)
        self.assertEqual(sorted(complete), sorted([name_of(0), name_of(90)]))
        self.assertEqual(incomplete, [])
        delete, _ = self.plan(lines, keep_min=1)
        self.assertEqual(delete, [name_of(90)])

    def test_refuses_unsafe_settings(self):
        for kwargs in ({"keep_min": 0}, {"keep_days": 0}, {"grace": 0}):
            with self.assertRaises(lib.RuleError, msg=str(kwargs)):
                self.plan(listing(0), **kwargs)

    def test_latest_complete_ignores_partial_and_foreign_names(self):
        lines = listing(5, 9) + listing(1, complete=False) + ["junk/COMPLETE"]
        run = subprocess.run([sys.executable, str(SCRIPTS / "hosting_backup_lib.py"), "latest-complete"],
                             input="\n".join(lines), capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, name_of(5)))
        empty = subprocess.run([sys.executable, str(SCRIPTS / "hosting_backup_lib.py"), "latest-complete"],
                               input="\n".join(listing(1, complete=False)), capture_output=True, text=True)
        self.assertEqual(empty.returncode, 3)

    def test_cli_prints_only_names_and_refuses_without_the_required_bundle(self):
        text = "\n".join(listing(0, 50, 60)) + "\n"
        run = lambda *extra: subprocess.run(  # noqa: E731
            [sys.executable, str(SCRIPTS / "hosting_backup_lib.py"), "prune-plan", "--keep-days", "30", "--keep-min", "1",
             "--now", NOW.isoformat(), *extra], input=text, capture_output=True, text=True)
        ok = run("--require", name_of(0))
        self.assertEqual((ok.returncode, ok.stdout.split()), (0, sorted([name_of(50), name_of(60)])))
        refused = run("--require", name_of(1))
        self.assertEqual((refused.returncode, refused.stdout), (3, ""))


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.file = self.dir / "abb_backup.prom"

    def test_write_is_atomic_readable_and_name_checked(self):
        lib.write_metrics(self.file, {lib.LAST_SUCCESS: 1760875200, "abb_hosting_backup_last_bundle_bytes": 1234.5})
        self.assertEqual(oct(self.file.stat().st_mode & 0o777), "0o644")
        self.assertEqual(lib.read_metrics(self.file)[lib.LAST_SUCCESS], 1760875200.0)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["abb_backup.prom"])  # no temp file left
        with self.assertRaises(lib.RuleError):
            lib.write_metrics(self.file, {"node_cpu": 1})

    def test_freshness_check_can_fail_every_way(self):
        now = 1_760_875_200.0
        # missing file: the KI-243 case, a backup that was never scheduled
        res = lib.check_freshness(self.file, max_age_hours=36, now=now)
        self.assertEqual(res[0][0], "FAIL")
        self.assertIn("never completed", res[0][1])
        # file without a success
        lib.write_metrics(self.file, {"abb_hosting_backup_last_run_success": 0})
        self.assertEqual(lib.check_freshness(self.file, max_age_hours=36, now=now)[0][0], "FAIL")
        # too old
        lib.write_metrics(self.file, {lib.LAST_SUCCESS: now - 37 * 3600})
        self.assertEqual(lib.check_freshness(self.file, max_age_hours=36, now=now)[0][0], "FAIL")
        # future
        lib.write_metrics(self.file, {lib.LAST_SUCCESS: now + 7200})
        self.assertEqual(lib.check_freshness(self.file, max_age_hours=36, now=now)[0][0], "FAIL")
        # fresh: PASS
        lib.write_metrics(self.file, {lib.LAST_SUCCESS: now - 3600, "abb_hosting_backup_last_run_success": 1})
        self.assertEqual([s for s, _ in lib.check_freshness(self.file, max_age_hours=36, now=now)], ["PASS"])
        # fresh success but the latest run failed: still a FAIL line
        lib.write_metrics(self.file, {lib.LAST_SUCCESS: now - 3600, "abb_hosting_backup_last_run_success": 0})
        self.assertEqual([s for s, _ in lib.check_freshness(self.file, max_age_hours=36, now=now)], ["PASS", "FAIL"])
        # garbage
        self.file.write_text("\x00\xff not metrics")
        self.assertEqual(lib.check_freshness(self.file, max_age_hours=36, now=now)[0][0], "FAIL")


class ContainerMetricsTests(unittest.TestCase):
    INSPECT = [
        {"Name": "/abb-api-1", "RestartCount": 5, "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}}, "State": {"OOMKilled": True, "Running": True}},
        {"Name": "/abb-web-1", "RestartCount": 0, "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}}, "State": {"OOMKilled": False, "Running": False}},
        {"Name": "/abb-migrate-1", "RestartCount": 0, "HostConfig": {"RestartPolicy": {"Name": "no"}}, "State": {"Running": False}},
        {"Name": 'bad"name\n', "RestartCount": 1, "HostConfig": {"RestartPolicy": {"Name": "always"}}, "State": {"Running": True}},
    ]

    def test_only_containers_meant_to_run_are_reported(self):
        samples = lib.container_samples(self.INSPECT, 1000.0)
        names = {labels.get("name") for _, labels, _ in samples if labels}
        self.assertNotIn("abb-migrate-1", names)
        value = {(n, labels.get("name")): v for n, labels, v in samples}
        self.assertEqual(value[("abb_hosting_container_restart_count", "abb-api-1")], 5.0)
        self.assertEqual(value[("abb_hosting_container_oom_killed", "abb-api-1")], 1.0)
        self.assertEqual(value[("abb_hosting_container_running", "abb-web-1")], 0.0)
        self.assertEqual(value[("abb_hosting_container_metrics_timestamp_seconds", None)], 1000.0)

    def test_file_is_atomic_escaped_and_parsable(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        target = d / "abb_containers.prom"
        lib.write_samples(target, lib.container_samples(self.INSPECT, 1000.0))
        text = target.read_text()
        self.assertIn('abb_hosting_container_oom_killed{name="abb-api-1"} 1', text)
        self.assertIn('name="bad\\"name\\n"', text)  # quote and newline escaped, never a raw newline inside a label
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["abb_containers.prom"])
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o644")
        with self.assertRaises(lib.RuleError):
            lib.write_samples(target, [("node_cpu", {}, 1)])

    def test_cli_reads_docker_inspect_from_stdin(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        run = subprocess.run([sys.executable, str(SCRIPTS / "hosting_backup_lib.py"), "containers", "--file", str(d / "c.prom")],
                             input=json.dumps(self.INSPECT), capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("abb_hosting_container_restart_count", (d / "c.prom").read_text())
        empty = subprocess.run([sys.executable, str(SCRIPTS / "hosting_backup_lib.py"), "containers", "--file", str(d / "e.prom")],
                               input="[]", capture_output=True, text=True)
        self.assertEqual(empty.returncode, 0)
        self.assertIn("metrics_timestamp", (d / "e.prom").read_text())


class ManifestTests(unittest.TestCase):
    def test_table_names_are_validated_before_they_reach_sql(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        (d / "MANIFEST.json").write_text(json.dumps({"tables": {"runs": 3, "events": 40}}))
        self.assertEqual(lib.manifest_counts(d), [("events", 40), ("runs", 3)])
        (d / "MANIFEST.json").write_text(json.dumps({"tables": {"runs; drop table x": 3}}))
        with self.assertRaises(lib.RuleError):
            lib.manifest_counts(d)
        (d / "MANIFEST.json").write_text(json.dumps({"tables": {"runs": -1}}))
        with self.assertRaises(lib.RuleError):
            lib.manifest_counts(d)


if __name__ == "__main__":
    unittest.main()
