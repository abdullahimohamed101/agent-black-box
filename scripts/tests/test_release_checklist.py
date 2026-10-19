"""Planted-violation tests for scripts/release_checklist.py (run: python3 -m unittest discover -s scripts/tests -v).

Each check must FAIL on a planted violation in a throwaway tree and pass on the clean tree. Secret-shaped
strings are assembled at run time so this file never contains one.
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

import release_checklist as rc

PAGE = "export default function Page() { return null; }\n"
CLEAN = {
    "README.md": "# Agent BlackBox\n\nA flight recorder for agents. Prerequisites: Python 3.12.\n",
    "docs/quickstart.md": "# Quickstart\n\nRun `make demo`.\n",
    "apps/web/src/app/w/[workspace]/projects/[project]/runs/page.tsx": PAGE,
    "apps/web/src/app/w/[workspace]/settings/members/page.tsx": PAGE,
    "apps/web/src/app/login/page.tsx": PAGE,
    "apps/web/src/lib/links.ts": (
        'export const a = (w: string) => `/w/${w}/projects/p/runs`;\n'
        'export const b = (w: string) => `/w/${w}/settings/members`;\n'
        'export const c = "/login";\n'
    ),
    "apps/web/src/components/charts.tsx": (
        "export function BarList({ bars }: { bars: number[] }) { return <ul>{bars.map((b) => <li key={b}>{b}</li>)}</ul>; }\n"
    ),
    "apps/api/src/abb_api/service.py": "import logging\nlog = logging.getLogger(__name__)\n\ndef f():\n    log.info('ok')\n",
}


def run(files: dict[str, str], *names: str, release: bool = False, extra: dict[str, bytes] | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for rel, data in (extra or {}).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        out = io.StringIO()
        with redirect_stdout(out):
            code = rc.run(root, list(names), release)
        return code, out.getvalue()


def planted(**more: str) -> dict[str, str]:
    return {**CLEAN, **more}


class EveryCheck(unittest.TestCase):
    def test_clean_tree_passes_every_static_check(self) -> None:
        names = [n for n in rc.CHECKS if n != "links"]
        code, out = run(CLEAN, *names)
        self.assertEqual(code, 0, out)

    def test_placeholders(self) -> None:
        for text in ("Lorem ipsum dolor sit amet", "TODO write this", "FIXME later", "[NEEDS INPUT: holder]"):
            code, out = run(planted(**{"README.md": text + "\n"}), "placeholders")
            self.assertEqual(code, 1, text)
            self.assertIn("README.md:1", out)

    def test_placeholder_in_web_src_and_docs(self) -> None:
        code, _ = run(planted(**{"apps/web/src/components/X.tsx": "// ok\nconst t = 'lorem ipsum';\n"}), "placeholders")
        self.assertEqual(code, 1)
        code, _ = run(planted(**{"docs/deployment.md": "TBD\n"}), "placeholders")
        self.assertEqual(code, 1)

    def test_internal_tracking_docs_are_not_scanned(self) -> None:
        code, _ = run(planted(**{"docs/KNOWN_ISSUES.md": "TODO\n", "docs/plans/active/p.md": "TODO\n"}), "placeholders")
        self.assertEqual(code, 0)

    def test_gated_markers_warn_then_fail_in_release_mode(self) -> None:
        files = planted(**{"docs/quickstart.md": "[CONFIRM AFTER STEP 10] remove\n", "README.md": "RELEASE_CLAIM: open source\n"})
        code, out = run(files, "placeholders")
        self.assertEqual(code, 0, out)
        self.assertIn("warn placeholders", out)
        code, out = run(files, "placeholders", release=True)
        self.assertEqual(code, 1)
        self.assertIn("FAIL placeholders docs/quickstart.md:1", out)
        self.assertIn("FAIL placeholders README.md:1", out)

    def test_confirm_helper_calls_in_site_source_are_gated(self) -> None:
        # The site writes markers as confirm("AFTER ..."); the rendered [CONFIRM ...] only exists in the export.
        files = planted(**{"site/src/components/X.tsx": '<p {...confirm("AFTER STEP 3: fact")}>x</p>\n'})
        code, out = run(files, "placeholders")
        self.assertEqual(code, 0, out)
        self.assertIn("warn placeholders site/src/components/X.tsx:1", out)
        code, out = run(files, "placeholders", release=True)
        self.assertEqual(code, 1)
        self.assertIn("FAIL placeholders site/src/components/X.tsx:1", out)

    def test_debug_logging(self) -> None:
        cases = {
            "apps/api/src/abb_api/a.py": "def f(x):\n    print(x)\n",
            "packages/sdk-python/src/blackbox/b.py": "breakpoint()\n",
            "integrations/openai/src/blackbox_openai/c.py": "log.debug('sent %s', payload)\n",
            "apps/web/src/components/D.tsx": "export const d = () => { console.log('x'); };\n",
            "apps/web/src/lib/e.ts": "function e() {\n  debugger;\n}\n",
        }
        for rel, text in cases.items():
            code, out = run(planted(**{rel: text}), "debug")
            self.assertEqual(code, 1, rel)
            self.assertIn(rel, out)

    def test_debug_ignores_tests_comments_and_methods(self) -> None:
        files = planted(**{
            "apps/api/tests/test_a.py": "print('fine in tests')\n",
            "apps/web/src/lib/x.ts": "// console.log('commented')\nconst s = 'debugger';\n",
            "apps/api/src/abb_api/m.py": "x = pprint.pprint(y)  # print( here is a comment\n",
        })
        code, out = run(files, "debug")
        self.assertEqual(code, 0, out)

    def test_secrets(self) -> None:
        shapes = {
            "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
            "key": "sk" + "-" + "a1b2c3d4e5f6g7h8i9j0k1",
            "github": "gh" + "p_" + "a" * 36,
            "pem": "-----BEGIN RSA " + "PRIVATE KEY-----",
            "abb": "abb_" + "live_" + "abcdefghijkl.secretsecret",
            "slack": "xo" + "xb-" + "1234567890-abcdef",
        }
        for kind, value in shapes.items():
            code, out = run(planted(**{"src/config.py": f'KEY = "{value}"\n'}), "secrets")
            self.assertEqual(code, 1, kind)
            self.assertIn("src/config.py:1", out)
            self.assertNotIn(value, out, "the matched value must never be printed")

    def test_secret_files(self) -> None:
        for name in (".env", "deploy/server.pem", "id_rsa"):
            code, out = run(planted(**{name: "x\n"}), "secrets")
            self.assertEqual(code, 1, name)
        code, _ = run(planted(**{".env.example": "ABB_X=1\n"}), "secrets")
        self.assertEqual(code, 0)

    def test_secrets_in_a_url(self) -> None:
        code, _ = run(planted(**{"a.py": 'U = "postgres://user:' + "hunter22secret" + '@db.internal/x"\n'}), "secrets")
        self.assertEqual(code, 1)
        code, _ = run(planted(**{"a.py": 'U = "postgres://user:${PASSWORD}@db/x"\n'}), "secrets")
        self.assertEqual(code, 0)

    def test_charts(self) -> None:
        cases = {
            "apps/web/src/components/A.tsx": "export const A = () => <svg>{Math.random()}</svg>;\n",
            "apps/web/src/components/B.tsx": "const series = [3, 5, 8, 13, 21];\nexport const B = () => <svg><polyline /></svg>;\n",
            "apps/web/src/components/C.tsx": "const d = [{ label: 'a', value: 4 }];\nexport const C = () => <div className='bar-fill' />;\n",
            "apps/web/src/components/charts.tsx": "export function Fake() { return <ul className='bar-fill' />; }\n",
        }
        for rel, text in cases.items():
            code, out = run(planted(**{rel: text}), "charts")
            self.assertEqual(code, 1, rel)
            self.assertIn(rel, out)

    def test_charts_fed_by_props_pass(self) -> None:
        files = planted(**{"apps/web/src/components/G.tsx": "export function G({ rows }: { rows: R[] }) { return <svg>{rows.map((r) => <rect key={r.id} />)}</svg>; }\n"})
        code, out = run(files, "charts")
        self.assertEqual(code, 0, out)

    def test_routes(self) -> None:
        for text in ('export const x = "/login/nowhere";\n', "export const x = (w: string) => `/w/${w}/settings/nope`;\n"):
            code, out = run(planted(**{"apps/web/src/lib/bad.ts": text}), "routes")
            self.assertEqual(code, 1, text)
            self.assertIn("bad.ts:1", out)

    def test_network(self) -> None:
        cases = {
            "site/src/app/layout.tsx": '<script src="https://cdn.example.com/x.js"></script>\n',
            "apps/web/src/app/g.css": "@import url('https://fonts.googleapis.com/css2?family=X');\n",
            "apps/web/src/lib/f.ts": "const r = await fetch('https://api.vendor.com/v1');\n",
            "apps/web/src/components/I.tsx": '<img src="https://images.example.org/a.png" />\n',
        }
        for rel, text in cases.items():
            code, out = run(planted(**{rel: text}), "network")
            self.assertEqual(code, 1, rel)
        ok = planted(**{"site/src/a.tsx": '<a href="https://github.com/x/y">repo</a>\n', "apps/web/src/b.ts": "fetch('http://localhost:8000/x');\n"})
        code, out = run(ok, "network")
        self.assertEqual(code, 0, out)

    def test_shots_budget(self) -> None:
        big = {"docs/screenshots/release/big.png": b"\0" * (rc.SHOT_MAX_BYTES + 1)}
        code, out = run(CLEAN, "shots", extra=big)
        self.assertEqual(code, 1, out)
        many = {f"docs/screenshots/release/{i}.png": b"\0" for i in range(rc.SHOT_MAX_COUNT + 1)}
        code, _ = run(CLEAN, "shots", extra=many)
        self.assertEqual(code, 1)
        ok = {f"docs/screenshots/release/{i}.png": b"\0" * 1000 for i in range(rc.SHOT_MAX_COUNT)}
        code, out = run(CLEAN, "shots", extra=ok)
        self.assertEqual(code, 0, out)

    def test_claims(self) -> None:
        for text in ("1,234 tests pass.", "Production-ready.", "A SaaS you can trust.", "Run it with --live today."):
            code, out = run(planted(**{"README.md": text + "\n"}), "claims")
            self.assertEqual(code, 1, text)
        code, out = run(planted(**{"README.md": "Real-model mode (--live) is available, untested by the project.\n"}), "claims")
        self.assertEqual(code, 0, out)

    def test_links(self) -> None:
        code, out = run(planted(**{"docs/a.md": "[gone](missing.md)\n"}), "links")
        self.assertEqual(code, 1, out)
        code, out = run(planted(**{"docs/a.md": "[here](quickstart.md)\n"}), "links")
        self.assertEqual(code, 0, out)

    def test_allowlist_exempts_only_the_named_line_and_stale_entries_fail(self) -> None:
        files = planted(**{
            "scripts/release-checklist-allow.txt": "debug | apps/api/src/abb_api/cli.py | print(| CLI output\n",
            "apps/api/src/abb_api/cli.py": "print('hi')\n",
            "apps/api/src/abb_api/other.py": "print('leak')\n",
        })
        code, out = run(files, "debug")
        self.assertEqual(code, 1)
        self.assertIn("other.py", out)
        self.assertNotIn("cli.py", out)
        stale = planted(**{"scripts/release-checklist-allow.txt": "debug | nothing/* | * | stale\n"})
        code, out = run(stale, *rc.CHECKS)
        self.assertIn("allow-list entry matches nothing", out)
        self.assertEqual(code, 1)

    def test_malformed_allowlist_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            run(planted(**{"scripts/release-checklist-allow.txt": "debug | only-two\n"}), "debug")


class RealTree(unittest.TestCase):
    def test_the_repository_passes_its_own_static_checks(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = rc.run(rc.HERE.parent, list(rc.CHECKS), release=False)
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
