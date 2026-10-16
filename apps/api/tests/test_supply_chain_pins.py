"""scripts/check_pins.py and scripts/audit_policy.py (Phase 19c step 7, KI-024, KI-060).

Fixture trees prove the checker fails on each unpinned shape and passes on pinned ones; a last
test runs it on the real repository so an unpinned reference cannot be merged.
"""

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SHA = "11d5960a326750d5838078e36cf38b85af677262"
DIGEST = "sha256:" + "ab" * 32


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


pins = _load("check_pins")
audit = _load("audit_policy")


def _tree(tmp_path: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return [f"{f.path}:{f.line}" for f in pins.run_checks(tmp_path, set())]


PINNED_WORKFLOW = f"""\
jobs:
  a:
    services:
      db:
        image: postgres:16-alpine@{DIGEST}
    steps:
      - uses: actions/checkout@{SHA} # v4.4.0
      - uses: ./.github/actions/local
"""


def test_a_fully_pinned_tree_passes(tmp_path: Path) -> None:
    files = {
        ".github/workflows/ci.yml": PINNED_WORKFLOW,
        "app/Dockerfile": (
            f"FROM python:3.12-slim@{DIGEST} AS build\n"
            f"COPY --from=ghcr.io/astral-sh/uv:0.12@{DIGEST} /uv /bin/uv\n"
            f"FROM scratch\nFROM build\nCOPY --from=build /x /y\n"
        ),
        "docker-compose.yml": f"services:\n  p:\n    image: prom/prometheus:v3@{DIGEST}\n",
        "scripts/run.sh": f"docker run --rm -v /a:/b --entrypoint sh busybox:1@{DIGEST} -c true\n",
    }
    assert _tree(tmp_path, files) == []


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (".github/workflows/x.yml", "steps:\n  - uses: actions/checkout@v4\n"),
        (".github/workflows/x.yml", "steps:\n  - uses: actions/checkout@main # main\n"),
        (".github/workflows/x.yml", f"steps:\n  - uses: actions/checkout@{SHA}\n"),
        (".github/workflows/x.yml", "steps:\n  - uses: docker://alpine:3.20\n"),
        (".github/workflows/x.yml", "s:\n  db:\n    image: postgres:16-alpine\n"),
        (".github/workflows/x.yml", "jobs:\n  a:\n    container: node:22\n"),
        ("a/Dockerfile", "FROM python:3.12-slim\n"),
        ("a/Dockerfile", "FROM --platform=linux/amd64 node:22 AS build\n"),
        (
            "a/Dockerfile",
            f"FROM python:3.12@{DIGEST}\nCOPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uv\n",
        ),
        ("a/Dockerfile", "FROM ${BASE_IMAGE}\n"),
        ("compose.override.yml", "services:\n  p:\n    image: grafana/grafana:12.1.0\n"),
        (
            "docker-compose.yml",
            f"services:\n  p:\n    image: x:1  # {DIGEST}\n",
        ),  # digest in a comment only
        ("scripts/run.sh", "docker pull busybox:1.36\n"),
        ("Makefile", "t:\n\tdocker run --rm -e A=b alpine:3.20 true\n"),
    ],
)
def test_each_unpinned_shape_fails(tmp_path: Path, rel: str, text: str) -> None:
    assert _tree(tmp_path, {rel: text}), f"{rel} should have been rejected: {text!r}"


def test_a_pinned_image_named_again_without_its_digest_fails(tmp_path: Path) -> None:
    files = {
        "docker-compose.yml": f"services:\n  d:\n    image: postgres:16-alpine@{DIGEST}\n",
        "scripts/helper.py": 'candidates = ["postgres:16-alpine", "alpine"]\n',
    }
    assert _tree(tmp_path, files) == ["scripts/helper.py:1"]


def test_the_allow_list_exempts_only_the_named_reference(tmp_path: Path) -> None:
    files = {
        "docker-compose.yml": "services:\n  a:\n    image: local-a:dev\n  b:\n    image: other:1\n"
    }
    _tree(tmp_path, files)
    found = pins.run_checks(tmp_path, {"local-a:dev"})
    assert [f.reference for f in found] == ["other:1"]


def test_the_real_repository_is_fully_pinned() -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "check_pins.py"), str(ROOT)],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_real_workflow_has_no_unpinned_action() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    uses = [line.split("uses:", 1)[1].strip() for line in text.splitlines() if "uses:" in line]
    assert uses
    for item in uses:
        assert item.startswith("./") or (
            "@" in item and len(item.split("@")[1].split()[0]) == 40 and "#" in item
        )


# ---- audit policy -------------------------------------------------------------------------------

TODAY = dt.date(2026, 10, 16)


def _report(
    fixes: list[str], aliases: list[str] | None = None, skip: str | None = None
) -> dict[str, object]:
    dep: dict[str, object] = {"name": "Foo", "version": "1.0"}
    if skip:
        dep["skip_reason"] = skip
    else:
        dep["vulns"] = [
            {"id": "PYSEC-1", "fix_versions": fixes, "aliases": aliases or ["CVE-2026-1"]}
        ]
    return {"dependencies": [dep]}


def test_audit_fails_a_fixable_vulnerability() -> None:
    failures, _ = audit.judge("", {"x": _report(["1.1"])}, TODAY)
    assert len(failures) == 1 and "fix available: 1.1" in failures[0]


def test_audit_fails_an_unfixed_vulnerability_until_it_is_allow_listed() -> None:
    failures, _ = audit.judge("", {"x": _report([])}, TODAY)
    assert len(failures) == 1 and "no fix released yet" in failures[0]
    allow = (
        "CVE-2026-1 | foo | 2026-12-01 | not reachable: we never parse untrusted input with it\n"
    )
    failures, notes = audit.judge(allow, {"x": _report([])}, TODAY)
    assert failures == [] and any("allowed until 2026-12-01" in n for n in notes)


def test_audit_rejects_expired_malformed_and_far_future_entries() -> None:
    expired = "PYSEC-1 | foo | 2026-10-01 | old\n"
    assert any("expired" in f for f in audit.judge(expired, {"x": _report([])}, TODAY)[0])
    assert audit.judge("PYSEC-1 | foo | soon | why\n", {}, TODAY)[0]
    assert audit.judge("PYSEC-1 | foo | 2030-01-01 | why\n", {}, TODAY)[0]
    assert audit.judge("PYSEC-1 | foo\n", {}, TODAY)[0]


def test_audit_entry_must_match_package_and_reports_stale_entries() -> None:
    wrong = "PYSEC-1 | bar | 2026-12-01 | why\n"
    failures, notes = audit.judge(wrong, {"x": _report([])}, TODAY)
    assert failures and any("stale" in n for n in notes)


def test_audit_fails_a_package_that_could_not_be_audited() -> None:
    failures, _ = audit.judge("", {"x": _report([], skip="not on PyPI")}, TODAY)
    assert failures and "not audited" in failures[0]


def test_audit_script_exit_codes(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("", encoding="utf-8")
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"dependencies": [{"name": "a", "version": "1", "vulns": []}]}))
    dirty = tmp_path / "dirty.json"
    dirty.write_text(json.dumps(_report(["2"])))
    script = str(ROOT / "scripts" / "audit_policy.py")
    ok = subprocess.run(  # noqa: S603
        [sys.executable, script, str(allow), f"c={clean}"], capture_output=True, check=False
    )
    bad = subprocess.run(  # noqa: S603
        [sys.executable, script, str(allow), f"d={dirty}"], capture_output=True, check=False
    )
    assert (ok.returncode, bad.returncode) == (0, 1)
