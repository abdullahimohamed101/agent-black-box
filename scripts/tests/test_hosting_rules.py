"""Self-test of the hosting alert rules (Phase 23, step 9): deploy/hosting/rules/host.yml and its promtool unit tests.

Always run: every alert in host.yml has a test that makes it fire AND a test that proves a healthy host stays silent (a structural
check over host.test.yml / host.long.test.yml), every alert has severity, summary, description and a runbook_url that points into
docs/runbooks/hosting-operations.md at a heading that exists.

HOSTING_RULES_MUTATION=1 (scripts/hosting-obs-e2e.sh sets it; needs Docker and the pinned Prometheus image): for each alert the
comparison in its expression is FLIPPED (> becomes <, == becomes !=) and promtool must then FAIL the tests that mention the alert.
A rule file whose tests still pass after its logic is inverted is not tested.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "deploy" / "hosting" / "rules"
PROMETHEUS = "prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e"
TEST_FILES = ("host.test.yml", "host.long.test.yml")
NOT_TESTABLE_AS_SILENT = {"Watchdog"}  # always firing by design


def alert_names(text: str) -> List[str]:
    return re.findall(r"(?m)^\s*- alert: (\w+)\s*$", text)


def cases(text: str) -> List[Tuple[str, bool]]:
    """[(alertname pattern, fires?)] from the promql_expr_test entries of one test file."""
    found = []
    pattern = re.compile(r'- expr: ALERTS\{alertname(=~?)"([^"]+)"[^\n]*\n\s+eval_time: [^\n]+\n\s+exp_samples:( \[\])?')
    for match in pattern.finditer(text):
        found.append((match.group(2) if match.group(1) == "=" else "~" + match.group(2), match.group(3) is None))
    return found


def covered(alerts: List[str]) -> Tuple[Dict[str, bool], Dict[str, bool]]:
    fires = {a: False for a in alerts}
    silent = {a: False for a in alerts}
    for name in TEST_FILES:
        for pattern, fire in cases((RULES / name).read_text(encoding="utf-8")):
            for alert in alerts:
                hit = re.fullmatch(pattern[1:], alert) if pattern.startswith("~") else pattern == alert
                if hit:
                    (fires if fire else silent)[alert] = True
    return fires, silent


class Coverage(unittest.TestCase):
    def test_every_alert_has_a_firing_case_and_a_silent_case(self):
        alerts = alert_names((RULES / "host.yml").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(alerts), 20)
        fires, silent = covered(alerts)
        self.assertEqual([a for a in alerts if not fires[a]], [], "alerts with no test that makes them fire")
        self.assertEqual([a for a in alerts if not silent[a] and a not in NOT_TESTABLE_AS_SILENT], [], "alerts with no test that proves silence")

    def test_the_coverage_check_itself_can_fail(self):
        fires, silent = covered(["HostDiskUsageHigh", "ThisAlertHasNoTest"])
        self.assertTrue(fires["HostDiskUsageHigh"])
        self.assertFalse(fires["ThisAlertHasNoTest"])
        self.assertFalse(silent["ThisAlertHasNoTest"])

    def test_alerts_are_documented(self):
        text = (RULES / "host.yml").read_text(encoding="utf-8")
        runbook = (REPO / "docs" / "runbooks" / "hosting-operations.md")
        headings = set()
        if runbook.exists():
            for line in runbook.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    title = line.lstrip("#").strip().lower()
                    headings.add(re.sub(r"[^a-z0-9 -]", "", title).replace(" ", "-"))
        blocks = re.split(r"(?m)^\s*- alert: ", text)[1:]
        for block in blocks:
            name = block.split("\n", 1)[0].strip()
            for key in ("severity:", "summary:", "description:", "runbook_url:"):
                self.assertIn(key, block, f"{name} lacks {key}")
            url = re.search(r"runbook_url: (\S+)", block)
            path, _, anchor = url.group(1).partition("#")  # type: ignore[union-attr]
            self.assertTrue((REPO / path).exists(), f"{name}: {path} does not exist")
            self.assertIn(anchor, headings, f"{name}: no heading '{anchor}' in {path}")


def flip(expr: str) -> str:
    """Invert the first comparison of an alert expression (or make vector(1) false)."""
    if re.fullmatch(r"\s*vector\(1\)\s*", expr):
        return "vector(1) == 0"
    swap = {">=": "<", "<=": ">", ">": "<", "<": ">", "==": "!="}
    match = re.search(r"(?<![=!<>~])(>=|<=|==|>|<)(?![=~])", expr)
    assert match, f"no comparison to flip in: {expr!r}"
    return expr[: match.start()] + swap[match.group(1)] + expr[match.end():]


def mutate(rules: str, alert: str) -> str:
    pattern = re.compile(rf"(- alert: {alert}\n\s+expr:)(.*?)(\n\s+(?:for|labels):)", re.S)
    match = pattern.search(rules)
    assert match, alert
    original = match.group(2)
    if original.strip().startswith("|"):
        body = original.strip()[1:]
        return rules[: match.start(2)] + " |" + flip_block(body) + rules[match.end(2):]
    return rules[: match.start(2)] + " " + flip(original.strip()) + rules[match.end(2):]


def flip_block(body: str) -> str:
    return flip(body)


class Flip(unittest.TestCase):
    def test_flip(self):
        self.assertEqual(flip("x > 80"), "x < 80")
        self.assertEqual(flip("up == 0"), "up != 0")
        self.assertEqual(flip("a >= 3"), "a < 3")
        self.assertEqual(flip('up{job=~"a|b"} == 0'), 'up{job=~"a|b"} != 0')
        self.assertEqual(flip("vector(1)"), "vector(1) == 0")


@unittest.skipUnless(os.environ.get("HOSTING_RULES_MUTATION") == "1" and shutil.which("docker"), "set HOSTING_RULES_MUTATION=1 (needs Docker)")
class Mutation(unittest.TestCase):
    def promtool(self, directory: str, test_file: str) -> int:
        return subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{directory}:/r:ro", PROMETHEUS, "test", "rules", f"/r/{test_file}"],
            capture_output=True, text=True, timeout=300,
        ).returncode

    def test_unmutated_tests_pass(self):
        for name in TEST_FILES:
            self.assertEqual(self.promtool(str(RULES), name), 0, name)

    def test_inverting_any_alert_makes_its_tests_fail(self):
        rules = (RULES / "host.yml").read_text(encoding="utf-8")
        survivors = []
        for alert in alert_names(rules):
            with tempfile.TemporaryDirectory(dir=str(REPO / ".local") if (REPO / ".local").is_dir() else None) as tmp:
                (Path(tmp) / "host.yml").write_text(mutate(rules, alert), encoding="utf-8")
                ran = False
                for name in TEST_FILES:
                    text = (RULES / name).read_text(encoding="utf-8")
                    if re.search(rf'alertname(=|=~)"[^"]*{alert}[^"]*"', text) or re.search(rf'alertname=~"[^"]*"', text) and any(
                        re.fullmatch(p[1:], alert) for p, _ in cases(text) if p.startswith("~")
                    ):
                        shutil.copy(RULES / name, Path(tmp) / name)
                        ran = True
                        if self.promtool(tmp, name) == 0:
                            survivors.append(f"{alert} (still passes {name})")
                self.assertTrue(ran, f"no test file mentions {alert}")
        self.assertEqual(survivors, [], "mutants that the tests did not kill")


if __name__ == "__main__":
    unittest.main()
