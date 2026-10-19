"""Tests for the documentation checkers (run: python3 -m unittest discover -s scripts/tests -v)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import check_docs_links as links
import docs_commands_check as cmds

FAKE = cmds.ROOT / "docs" / "x.md"


def block(text: str, confirm: bool = False, mode: str = "reference") -> cmds.Block:
    return cmds.Block(FAKE, 1, mode, "", text, confirm)


class CommandChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = cmds.Repo()

    def test_existing_target_and_path_pass(self) -> None:
        problems, _ = cmds.check_reference(block("make up\nmake seed\nls scripts/smoke.sh"), self.repo)
        self.assertEqual(problems, [])

    def test_missing_target_path_env_profile_and_cli_fail(self) -> None:
        text = (
            "make nonsense-target\nls scripts/does-not-exist.sh\nexport ABB_NOT_A_REAL_SETTING=1\n"
            "docker compose --profile bogus up\npython -m abb_api.cli no-such-command"
        )
        problems, _ = cmds.check_reference(block(text), self.repo)
        self.assertEqual(len(problems), 5, problems)

    def test_marker_tolerates_only_the_demo_target(self) -> None:
        if "demo" in self.repo.targets:
            self.skipTest("make demo exists: the marker is now stale by design")
        ok, tolerated = cmds.check_reference(block("make demo", confirm=True), self.repo)
        self.assertEqual((ok, tolerated), ([], 1))
        bad, _ = cmds.check_reference(block("make demo", confirm=False), self.repo)
        self.assertEqual(len(bad), 1)
        other, _ = cmds.check_reference(block("make nonsense-target", confirm=True), self.repo)
        self.assertEqual(len(other), 1)

    def test_marker_is_stale_once_the_target_exists(self) -> None:
        self.repo.targets.add("demo")
        problems, _ = cmds.check_reference(block("make demo", confirm=True), self.repo)
        self.assertEqual(len(problems), 1)
        self.assertIn("remove", problems[0])

    def test_run_refusals(self) -> None:
        self.assertIsNone(cmds.run_refusal(block('echo "K=$(openssl rand -hex 8)"', mode="run")))
        for text in ("docker compose up", "curl localhost:8000", "make up", "rm -rf x", "git push", "cat .env"):
            self.assertIsNotNone(cmds.run_refusal(block(text, mode="run")), text)
        self.assertIsNotNone(cmds.run_refusal(block("perl -e 1", mode="run")))

    def test_parse_blocks_reads_the_marker_above_a_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.md"
            path.write_text(
                "<!-- check:run -->\n```bash\necho a\n```\n\n<!-- check:skip: needs a stack -->\n```bash\nmake up\n```\n"
                "```bash\nmake seed\n```\n```python\nprint(1)\n```\n",
                encoding="utf-8",
            )
            modes = [(b.mode, b.reason) for b in cmds.parse_blocks(path)]
        self.assertEqual(modes, [("run", ""), ("skip", "needs a stack"), ("reference", "")])


class LinkChecks(unittest.TestCase):
    def test_github_slugs(self) -> None:
        self.assertEqual(links.slug("Hello, World `x`"), "hello-world-x")
        self.assertEqual(links.slug("A/B (c) -d"), "ab-c--d")

    def test_broken_and_good_links(self) -> None:
        with tempfile.TemporaryDirectory(dir=links.ROOT) as tmp:
            root = Path(tmp)
            (root / "b.md").write_text("# Target\n\n## Same\n\n## Same\n", encoding="utf-8")
            page = root / "a.md"
            page.write_text(
                "[ok](b.md#target) [dup](b.md#same-1) [self](#here)\n# Here\n"
                "[no file](zzz.md) [no anchor](b.md#nope) [ext](https://example.com/x)\n"
                "```\n[ignored](zzz.md)\n```\n`[ignored](zzz.md)`\n",
                encoding="utf-8",
            )
            external: list[tuple[Path, int, str]] = []
            problems = links.check_file(page, external)
        self.assertEqual(len(problems), 2, problems)
        self.assertEqual(len(external), 1)


if __name__ == "__main__":
    unittest.main()
