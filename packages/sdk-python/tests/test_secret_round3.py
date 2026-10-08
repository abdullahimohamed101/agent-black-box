"""Round-three reproductions (independent adversarial pass): each of these leaked before its fix.

Same method as test_secret_leaks.py: a real recorder against a capturing server; a leak is a planted secret
appearing anywhere the server received, in an event attribute or in what the model is shown.
"""

import base64
import html
import json
import shlex
import time
from pathlib import Path

import pytest

from blackbox import BlackBox, PayloadMode
from blackbox.coding import CodingRecorder, parse_test_output
from blackbox.secretscan import SecretFiles, extract_values
from tests.test_artifacts import ArtifactServer, server  # noqa: F401  (fixture)
from tests.test_secret_leaks import everything, git, make


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    for args in (["init", "-b", "main"], ["config", "user.name", "t"], ["config", "user.email", "t@e.x"],
                 ["config", "commit.gpgsign", "false"]):  # fmt: skip
        git(tmp_path, *args)
    (tmp_path / "a.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "init")
    return tmp_path


def leaked(bb: BlackBox, srv: ArtifactServer, results: list, secrets: list[str]) -> list[str]:
    text = everything(bb, srv, results)
    return [s for s in secrets if s in text]


# 1. a value must survive its file being moved or deleted ------------------------------------------------


@pytest.mark.parametrize(
    "commands",
    [
        ["mv .env moved.txt", "sed 's/.*=//' moved.txt"],
        ["mkdir -p build && cp .env build/x.txt", "rm .env", "cat build/x.txt"],
        ["mv .env ../outside.txt 2>/dev/null || mv .env gone.dat", "cat gone.dat"],
    ],
)
def test_a_value_is_still_masked_after_its_file_moves_or_disappears(
    plain: Path,
    server: ArtifactServer,  # noqa: F811
    commands: list[str],
) -> None:
    (plain / ".env").write_text("SVC_PW=moved-away-secret-8841\n")
    bb, _run, rec = make(plain, server)
    results = [rec.run_command(c, timeout=10) for c in commands]
    assert leaked(bb, server, results, ["moved-away-secret-8841"]) == []
    bb.shutdown()


# 2. placeholders must not turn ordinary words into masks ---------------------------------------------------


def test_placeholder_values_in_an_example_file_do_not_corrupt_the_agents_view(
    plain: Path,
    server: ArtifactServer,  # noqa: F811
) -> None:
    (plain / ".env.example").write_text(
        "SECRET_KEY=secret\nDB_PASSWORD=password\nAPI_TOKEN=test\nCOOKIE_SECURE=true\nSESSION_TTL=3600\n"
    )
    (plain / "app.py").write_text("def check_secret(password):\n    return password == 'test'\n")
    (plain / "t.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    def test_b(self): pass\n"
        "unittest.main()\n"
    )
    bb, _run, rec = make(plain, server)
    assert rec.read_file("app.py") == "def check_secret(password):\n    return password == 'test'\n"
    result = rec.run_command("python3 t.py", timeout=20)
    assert "Ran 2 tests" in result.output and "REDACTED" not in result.output
    summary = parse_test_output(result.output)
    assert summary is not None and summary.total == 2
    bb.shutdown()


# 3. secrets that only exist in history ----------------------------------------------------------------------


def test_a_rotated_or_deleted_secret_in_history_is_masked(
    plain: Path,
    server: ArtifactServer,  # noqa: F811
) -> None:
    (plain / ".env").write_text("DB_PW=hist-secret-first-5521\n")
    git(plain, "add", "-A")
    git(plain, "commit", "-m", "add env")
    (plain / ".env").write_text("DB_PW=hist-secret-rotated-6632\n")
    git(plain, "commit", "-am", "rotate")
    git(plain, "rm", "-q", ".env")
    git(plain, "commit", "-m", "remove env")
    assert not (plain / ".env").exists()  # nothing sensitive left in the tree
    bb, _run, rec = make(plain, server)
    shas = rec.run_command("git log --all --format=%H -- .env", timeout=10).output.split()
    blobs = [
        rec.run_command(f"git rev-parse {sha}:.env", timeout=10).output.strip() for sha in shas[1:]
    ]
    results = []
    for blob in blobs:
        if blob:
            results += [
                rec.run_command(f"git show {blob}", timeout=10),
                rec.run_command(f"git cat-file -p {blob}", timeout=10),
            ]
    results.append(rec.run_command("git log -p --all", timeout=10))
    assert leaked(bb, server, results, ["hist-secret-first-5521", "hist-secret-rotated-6632"]) == []
    bb.shutdown()


# 4. credentials in remotes and .git/config -------------------------------------------------------------------


@pytest.mark.parametrize(
    "show",
    ["git remote -v", "git config --list", "cat .git/config", "git config --get remote.origin.url"],
)
def test_a_bare_token_in_a_remote_url_is_masked(
    plain: Path,
    server: ArtifactServer,  # noqa: F811
    show: str,
) -> None:
    token = "bareTokenUser0123456789abcdef"
    bb, _run, rec = make(plain, server)
    results = [
        rec.run_command(f"git remote add origin https://{token}@example.com/o/r.git", timeout=10),
        rec.run_command(show, timeout=10),
    ]
    assert leaked(bb, server, results, [token]) == []
    bb.shutdown()


# 5. the agent's own (host) environment -------------------------------------------------------------------------


def test_every_host_environment_value_is_masked_whatever_its_name(
    plain: Path,
    server: ArtifactServer,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = {"SMTP_PW": "smtp-host-secret-7781", "STRIPE_SK": "stripe-host-secret-7782", "MYPASS": "mypass-host-secret-7783",
            "MONGO_URI": "mongodb://u:mongo-host-secret-7784@h/db", "HF_TOK": "hf-host-secret-7785"}  # fmt: skip
    for key, value in host.items():
        monkeypatch.setenv(key, value)
    bb, _run, rec = make(plain, server)
    results = [
        rec.run_command("ps eww -p $PPID", timeout=10),
        rec.run_command("ps eww -p $$", timeout=10),
    ]
    assert leaked(bb, server, results, list(host.values())) == []
    assert all(v not in rec.sanitize(f"x {v} y") for v in host.values())
    bb.shutdown()


# 7. discovery: do not skip real locations, do not give up silently -------------------------------------------


@pytest.mark.parametrize("where", ["build", "dist", "target", ".cache", "zz/deep/er"])
def test_secret_files_are_found_in_build_output_directories_and_deep_paths(
    tmp_path: Path, where: str
) -> None:
    (tmp_path / where).mkdir(parents=True)
    (tmp_path / where / ".env").write_text("A_KEY=found-in-odd-place-4410\n")
    assert "found-in-odd-place-4410" in SecretFiles(tmp_path).refresh()


def test_a_huge_tree_either_finds_the_secret_or_says_the_scan_was_incomplete(
    tmp_path: Path,
) -> None:
    big = tmp_path / "a"
    big.mkdir()
    for i in range(21_000):
        (big / f"f{i}.txt").write_text("")
    (tmp_path / "zz").mkdir()
    (tmp_path / "zz" / ".env").write_text("A_KEY=after-twenty-thousand-files-9915\n")
    scan = SecretFiles(tmp_path)
    started = time.perf_counter()
    values = scan.refresh()
    assert time.perf_counter() - started < 10  # bounded by its own time budget
    assert "after-twenty-thousand-files-9915" in values or scan.incomplete


# 8. file shapes ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("master.key", "0123456789abcdef0123456789abcdef\n", {"0123456789abcdef0123456789abcdef"}),
        ("api.key", "rawtokenvalue-A1b2C3d4\n", {"rawtokenvalue-A1b2C3d4"}),
        ("creds.yml", "db:\n  pass: |\n    block-line-one-77\n    block-line-two-88\nlist:\n  - list-secret-item-99\n", {"block-line-one-77", "block-line-two-88", "list-secret-item-99"}),
        (".env", 'MULTI="first-line-secret-11\nsecond-line-secret-22"\nOTHER=1\n', {"first-line-secret-11", "second-line-secret-22"}),
        (".env", "TOK=value-before-comment-33 # trailing note\n", {"value-before-comment-33"}),
        ("secrets.json", '{"webhook": "ZqxKmwPlrAbcdEfGh", "admin": "correct horse battery staple"}', {"ZqxKmwPlrAbcdEfGh", "correct horse battery staple"}),
    ],
)  # fmt: skip
def test_more_secret_file_shapes_are_learned(name: str, text: str, expected: set[str]) -> None:
    got = extract_values(name, text)
    assert expected <= got, expected - got


# 9. natural escaping and encoding of a special-character secret ---------------------------------------------------


def test_an_escaped_or_encoded_special_character_secret_is_still_masked(plain: Path) -> None:
    secret = "Ab\"Cd'Ef\\Gh$Ij/Kl&Mn<Op> Qr~s é9"
    (plain / ".env").write_text(f"DB_PASSWORD={secret}\n")
    bb = BlackBox(mode="offline", project="d", payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, plain)
        forms = {
            "json": json.dumps(secret), "json-ascii": json.dumps(secret, ensure_ascii=False),
            "repr": repr(secret), "shlex": shlex.quote(secret), "html": html.escape(secret),
            "b64-plain": base64.b64encode(secret.encode()).decode(),
            "b64-prefixed-x": base64.b64encode(b"x" + secret.encode()).decode(),
            "b64-prefixed-user": base64.b64encode(b"user:" + secret.encode()).decode(),
            "b64-json": base64.b64encode(json.dumps({"k": secret}).encode()).decode(),
        }  # fmt: skip
        for name, form in forms.items():
            out = rec.sanitize(f"before {form} after")
            assert "Ab" not in out.replace("Abc", "") or "[REDACTED" in out, name
            assert "Kl&Mn" not in out and "Kl\\u0026" not in out and "S2w6" not in out, name
        assert "Ab\"Cd'Ef" not in rec.sanitize(f"cut: {secret[:20]}")  # a prefix of the value


# 10. redaction must not rewrite ordinary code ---------------------------------------------------------------------


def _bb() -> BlackBox:
    return BlackBox(mode="offline", project="d", payload_mode=PayloadMode.FULL)


@pytest.mark.parametrize(
    "code",
    [
        "return password == 'test'",
        "if token === other:",
        "assert secret_key != None and api_key == expected",
        "const k = (password) => password === x",
        "if password =~ /abc/",
    ],
)
def test_comparisons_in_source_code_are_not_treated_as_assignments(code: str) -> None:
    assert _bb().redact_text(code) == code


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password=hunter2hunter2", "hunter2hunter2"),
        ("password: hunter2hunter2", "hunter2hunter2"),
        ("DB_PASSWORD = 'hunter2hunter2'", "hunter2hunter2"),
        ("api_key := hunter2hunter2", "hunter2hunter2"),
        ("GH_PAT=ghpvalue123456", "ghpvalue123456"),
    ],
)
def test_real_assignments_are_still_redacted(text: str, secret: str) -> None:
    assert secret not in _bb().redact_text(text)
