"""Planted-bypass tests for scripts/check_site_claims.py (run: python3 -m unittest discover -s scripts/tests -v).

The claims gate reads the exported HTML; each case plants one way a gated or banned claim could slip past a literal
substring match (look-alike hyphens, soft hyphens, entities, script text, upper-case hrefs) and must be caught.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import check_site_claims as csc


def page(body: str = "<p>Hello.</p>", flag: str = "0", head: str = "") -> str:
    return (
        f'<html><head><meta name="abb-release-claims" content="{flag}">{head}</head>'
        f"<body><main>{body}</main><footer>{csc.STATUS}</footer></body></html>"
    )


def run(html: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "index.html").write_text(html, encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = csc.main([tmp])
        return code, out.getvalue()


class Claims(unittest.TestCase):
    def test_clean_page_passes(self) -> None:
        code, out = run(page())
        self.assertEqual(code, 0, out)

    def test_gated_claims_are_caught_despite_look_alike_spelling(self) -> None:
        cases = {
            "non-breaking hyphen (U+2011)": "<p>It is self‑hosted.</p>",
            "a space instead of a hyphen": "<p>Self hosted by you.</p>",
            "a non-breaking space": "<p>Self hosted by you.</p>",
            "a soft hyphen entity": "<p>It is open&shy;source.</p>",
            "a numeric entity": "<p>It is open&#8209;source.</p>",
            "an en dash": "<p>It is open–source.</p>",
            "full-width letters (NFKC)": "<p>ｏｐｅｎ source.</p>",
            "a zero-width space": "<p>self​-hosted.</p>",
            "text inside a script": '<script>window.__x = "Open source. Self-hosted."</script>',
            "JSON in a script tag": '<script type="application/json">{"t":"Source on GitHub"}</script>',
        }
        for what, body in cases.items():
            with self.subTest(what):
                code, out = run(page(body))
                self.assertEqual(code, 1, out)
                self.assertIn("gated claim", out)

    def test_repository_links_are_caught_in_any_case_or_entity(self) -> None:
        for href in ("HTTPS://GITHUB.COM/o/r", "https://www.GitHub.com/o/r", "https://github&#46;com/o/r", " https://github.com/o/r"):
            with self.subTest(href):
                code, out = run(page(f'<a href="{href}">code</a>'))
                self.assertEqual(code, 1, out)
                self.assertIn("links into the repository", out)

    def test_a_clone_command_in_the_payload_is_text_but_a_link_prop_is_not(self) -> None:
        clone = '<script>self.__next_f.push([1,"git clone https://github.com/o/r.git"])</script>'
        code, out = run(page(clone))
        self.assertEqual(code, 0, out)
        link = '<script>self.__next_f.push([1,"{\\"href\\":\\"https://GitHub.com/o/r\\"}"])</script>'
        code, out = run(page(link))
        self.assertEqual(code, 1, out)
        self.assertIn("link prop", out)

    def test_the_same_claims_pass_with_the_flag_on(self) -> None:
        code, out = run(page("<p>Open source. Self-hosted.</p><a href=\"https://github.com/o/r\">x</a>", flag="1"))
        self.assertEqual(code, 0, out)

    def test_banned_words_survive_soft_hyphens_and_entities(self) -> None:
        for body in ("<p>A pow&shy;erful tool.</p>", "<p>A seam­less tool.</p>", "<p>A&nbsp;SaaS&nbsp;tool.</p>"):
            with self.subTest(body):
                code, _ = run(page(body))
                self.assertEqual(code, 1)

    def test_the_langgraph_qualifier_is_required_at_every_mention(self) -> None:
        ok = "<p>LangGraph adapter, tested offline.</p>"
        far = "<p>" + ("word " * 120) + "</p>"
        code, out = run(page(ok + far + "<p>Works with LangGraph.</p>"))
        self.assertEqual(code, 1, out)
        self.assertIn("LangGraph", out)
        code, out = run(page(ok))
        self.assertEqual(code, 0, out)

    def test_live_mode_is_not_allowed_outside_the_footer(self) -> None:
        code, _ = run(page("<p>Run it with --live today.</p>"))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
