"""Regression tests for every way a command can print a secret file (ADR-031 value masking).

Real BlackBox against a capturing stub server: nothing the agent runs may put a planted secret into an artifact body,
an artifact name, an event, or the output handed back to the model, however the command reads the file.
"""

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from blackbox import BlackBox, PayloadMode
from blackbox.coding import CodingRecorder, classify_command
from blackbox.secretscan import (
    MAX_FILE_BYTES,
    SecretFiles,
    extract_values,
    is_sensitive_path,
    withhold_sensitive_hunks,
)
from tests.test_artifacts import ArtifactServer, server  # noqa: F401  (fixture)

OLD = "OLD-committed-pw-7781"
NEW = "sup3r-s3cret-pw-4412"
TOK = "tok_zz_unknown_shape_9921"
SECRETS = [OLD, NEW, TOK, "local-only-secret-5530"]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for args in (["init", "-b", "main"], ["config", "user.name", "t"], ["config", "user.email", "t@e.x"],
                 ["config", "commit.gpgsign", "false"]):  # fmt: skip
        git(tmp_path, *args)
    (tmp_path / ".env").write_text(f"DB_PW={OLD}\nPORT=8000\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / ".env.local").write_text("LOCAL_KEY=local-only-secret-5530\n")
    (tmp_path / "a.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "init")
    (tmp_path / ".env").write_text(f"DB_PW={NEW}\nAPI_TOKEN={TOK}\nPORT=8000\n")
    (tmp_path / "a.py").write_text("x = 2\n")
    git(tmp_path, "stash", "push", "-m", "wip")
    (tmp_path / ".env").write_text(f"DB_PW={NEW}\nAPI_TOKEN={TOK}\nPORT=8000\n")
    (tmp_path / "a.py").write_text("x = 3\n")
    return tmp_path


def make(root: Path, srv: ArtifactServer) -> tuple[BlackBox, Any, CodingRecorder]:
    bb = BlackBox(
        api_key="abb_live_k.secret", endpoint=srv.url, project="d", payload_mode=PayloadMode.FULL,
        mode="http", wait=lambda _s: True,
    )  # fmt: skip
    run = bb.run("r")
    return bb, run, CodingRecorder(bb, run, root)


def everything(bb: BlackBox, srv: ArtifactServer, results: list[Any]) -> str:
    assert bb.flush(5)
    bodies = [p.body.decode(errors="replace") + p.path + repr(p.query) for p in srv.received]
    return "\n".join(
        [*bodies, repr(bb.buffered_events()), *(r.output + r.command for r in results)]
    )


CASES = [
    "cat .env; echo done", "cat .env|head", "source .env; echo $DB_PW", "cat<.env", "(cat .env)",
    "bash -c 'cat .env'", 'sh -c "cat .env"', "python3 -c 'print(open(\".env\").read())'",
    "node -e 'console.log(require(\"fs\").readFileSync(\".env\",\"utf8\"))'",
    "ruby -e 'puts File.read(\".env\")'", "cat .e*", "cat .en?", "cat $(ls -a | grep env)",
    "D=.env; cat $D", "dd if=.env", "grep -r DB_PW .", "grep -rn DB_PW", "grep -R DB_PW .",
    "find . -type f -exec cat {} +", "find . -name '.env*' | xargs cat", "tar cf - . | tar xO",
    "git grep DB_PW", "git archive HEAD | tar xO", "git show HEAD:.env", "git show :.env",
    "git show 'stash@{0}:.env'", "git cat-file -p HEAD:.env", "git cat-file blob HEAD:.env",
    "git diff --no-prefix", "git diff --src-prefix=x/ --dst-prefix=y/", "git diff --color=always",
    "git -c color.ui=always diff", "git -c color.ui=always show stash@{0}", "git -c color.ui=always log -p --all",
    "git diff --color-words", "git show stash@{0}", "git diff -c HEAD", "git stash show -p", "git log -p --all",
    "cat app/.env.local", "cat ./app/../.env", "less .env", "head -c 100 .env", "strings .env", "base64 .env",
    "xxd .env", "od -c .env", "awk '{print}' .env", "sed -n p .env", "cp .env /tmp/x.out && cat /tmp/x.out",
    "ls -a | xargs cat", "echo $(cat .env)", "printf '%s' \"`cat .env`\"", "while read l; do echo $l; done < .env",
    "python3 - <<'EOF'\nprint(open('.env').read())\nEOF", "env -i cat .env", "nl .env", "tac .env", "rev .env",
]  # fmt: skip


@pytest.mark.parametrize("command", CASES)
def test_no_command_shape_leaks_a_planted_secret(
    repo: Path,
    server: ArtifactServer,  # noqa: F811
    command: str,
) -> None:
    bb, _run, rec = make(repo, server)
    result = rec.run_command(command, timeout=10)
    text = everything(bb, server, [result])
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked through: {command!r}"
    bb.shutdown()


def test_the_recorders_own_git_diff_and_file_diffs_do_not_leak(
    repo: Path,
    server: ArtifactServer,  # noqa: F811
) -> None:
    bb, _run, rec = make(repo, server)
    results = [rec.run_command("git diff")]
    rec.git_diff()
    rec.write_file(".env", f"DB_PW={NEW}\nNEW_SECRET=brand-new-secret-123\n")
    rec.write_file(
        "app/cfg.py", f"PASSWORD = '{NEW}'\n"
    )  # an ordinary file that carries a known secret
    text = everything(bb, server, results)
    for secret in [*SECRETS, "brand-new-secret-123"]:
        assert secret not in text
    bb.shutdown()


def test_the_model_never_sees_a_secret_from_read_file(repo: Path) -> None:
    bb = BlackBox(mode="offline", project="d", payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, repo)
        assert NEW not in rec.read_file(".env") and NEW not in rec.read_file("app/../.env")


def test_a_secret_written_by_a_command_is_masked_straight_away(repo: Path) -> None:
    bb = BlackBox(mode="offline", project="d", payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, repo)
        result = rec.run_command(
            "echo FRESH_PW=created-by-command-6612 > .env.prod; echo created-by-command-6612"
        )
    assert "created-by-command-6612" not in result.output


def test_encoded_forms_of_a_secret_are_masked(repo: Path) -> None:
    import base64
    from urllib.parse import quote

    (repo / ".env").write_text("DB_PW=p@ss/w0rd+x\n")
    bb = BlackBox(mode="offline", project="d", payload_mode=PayloadMode.FULL)
    with bb.run("r") as run:
        rec = CodingRecorder(bb, run, repo)
        b64 = base64.b64encode(b"p@ss/w0rd+x").decode()
        out = rec.sanitize(f"a {b64} b {quote('p@ss/w0rd+x', safe='')} c p@ss/w0rd+x")
    assert "p@ss" not in out and b64 not in out and "w0rd" not in out


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        (".env", "export A_KEY='quoted value 123'\nDB_PASSWORD=q9Zx2Lp\nPORT=8000\nHOST=localhost\n# C=comment", {"quoted value 123", "q9Zx2Lp"}),
        ("secrets.json", '{"db": {"password": "pw-1234"}, "n": 5, "name": "plainname"}', {"pw-1234", "plainname"}),
        ("creds.yml", "api_key: abcDEF123456\nname: demo\n", {"abcDEF123456"}),
        ("k.pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxxxxxxxx\nAAAAAAAAAAAAAAAAAAAA\n-----END RSA PRIVATE KEY-----", {"MIIEowIBAAKCAQEAxxxxxxxx", "AAAAAAAAAAAAAAAAAAAA"}),
        (".pgpass", "db:5432:app:user:hunter2hunter2\n", {"db:5432:app:user:hunter2hunter2", "hunter2hunter2"}),
        (".htpasswd", "bob:$apr1$abc$defghijkl\n", {"bob:$apr1$abc$defghijkl", "$apr1$abc$defghijkl"}),
        (".git-credentials", "https://user:tokenvalue99@github.com\n", {"tokenvalue99", "https://user:tokenvalue99@github.com"}),
        (".npmrc", "//registry.npmjs.org/:_authToken=npm_abcdefghijklmnopqrstuvwxyz0123456789\n", {"npm_abcdefghijklmnopqrstuvwxyz0123456789"}),
        (".netrc", "machine h login me password s3cretpw\n", {"s3cretpw"}),
    ],
)  # fmt: skip
def test_value_extraction(name: str, text: str, expected: set[str]) -> None:
    got = extract_values(name, text)
    assert expected <= got, expected - got
    assert "localhost" not in got and "8000" not in got
    if name != "secrets.json":  # every string leaf of a file that is secret BY NAME is learned
        assert "plainname" not in got
    assert "me" not in got  # too short to mask anywhere: it would corrupt all output


@pytest.mark.parametrize(
    "path",
    [".env", ".ENV", "a/.Env.Local", "prod.env", ".envrc", ".env~", ".env_prod", ".env-prod", "env.local", ".pgpass",
     ".htpasswd", "home/.docker/config.json", "x.tfstate", "prod.tfvars", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa.pub",
     "a.jks", "a.ppk", "a.gpg", "a.PEM", "k.key", "a.p12", "kubeconfig", ".kube/config", ".netrc", ".npmrc", ".pypirc",
     "credentials", ".aws/credentials", "secrets.yaml", "app_secret.json", "db-secrets.toml"],
)  # fmt: skip
def test_sensitive_names_case_insensitive(path: str) -> None:
    assert is_sensitive_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "environment.py",
        "env.py",
        "app/session.py",
        "config.json",
        "docker/config.jsonx",
        "README.md",
    ],
)
def test_ordinary_names_are_not_sensitive(path: str) -> None:
    assert not is_sensitive_path(path)


COLOUR_DIFF = (
    "\x1b[1mdiff --git a/.env b/.env\x1b[m\nindex 1..2 100644\n\x1b[1m--- a/.env\x1b[m\n\x1b[1m+++ b/.env\x1b[m\n"
    "@@ -1 +1 @@\n-DB_PW=old\n+DB_PW=new-secret\ndiff --git a/ok.py b/ok.py\n--- a/ok.py\n+++ b/ok.py\n@@ -1 +1 @@\n-a\n+b\n"
)


@pytest.mark.parametrize(
    "text",
    [
        COLOUR_DIFF,
        "diff --git .env .env\n--- .env\n+++ .env\n@@ -1 +1 @@\n-x\n+new-secret\n",
        "diff --git x/.env y/.env\n--- x/.env\n+++ y/.env\n@@ -1 +1 @@\n-x\n+new-secret\n",
        "diff --cc .env\nindex 1,2..3\n--- a/.env\n+++ b/.env\n@@@ -1,1 -1,1 +1,1 @@@\n- x\n++new-secret\n",
        "diff --combined .env\n@@@ -1 -1 +1 @@@\n++new-secret\n",
        "--- a/.env\t2026\n+++ b/.env\t2026\n@@ -1 +1 @@\n-x\n+new-secret\n",
        "diff --git a/.ENV b/.ENV\n@@ -1 +1 @@\n+new-secret\n",
        "diff --git a/old b/.env\nrename from old\nrename to .env\n@@ -1 +1 @@\n+new-secret\n",
    ],
)
def test_diff_section_filter_handles_every_header_shape(text: str) -> None:
    out = withhold_sensitive_hunks(text)
    assert "new-secret" not in out and "withheld" in out


def test_other_sections_of_a_diff_survive() -> None:
    out = withhold_sensitive_hunks(COLOUR_DIFF)
    assert "+b" in out and "\x1b" not in out


def test_the_scanner_bounds_itself(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / ".env").write_text("A_KEY=vendored-secret-value-1\n")
    (tmp_path / "huge").mkdir()
    (tmp_path / "huge" / ".env").write_text(
        "C_KEY=" + "y" * (MAX_FILE_BYTES + 10) + "\n"
    )  # over the cap
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".env").write_text("B_KEY=scanned-value-77\n")
    values = SecretFiles(tmp_path).refresh()
    assert "scanned-value-77" in values
    assert "vendored-secret-value-1" in values  # a secret file is found even inside a package tree
    assert not any(v.startswith("yyyy") for v in values)  # a file over the size cap is not read


def test_a_very_long_value_is_learned_exactly_without_exploding_the_scan(tmp_path: Path) -> None:
    """Regression: a 300,000-character value took ~10 minutes (a regex re-scanned the run from every position)."""
    blob = "Zq9" * 100_000
    (tmp_path / ".env").write_text(f"BIG_KEY={blob}\n")
    started = time.perf_counter()
    values = SecretFiles(tmp_path).refresh()
    assert time.perf_counter() - started < 5
    assert (
        blob in values
    )  # still masked exactly (head/tail forms too), only the derived encodings are skipped


@pytest.mark.parametrize(
    "shape", ["x", "A_KEY=x", 'A_KEY="a', "A_KEY='a", "a=b\n", "k: |\n  x\n", ":", "-", "a.", '"']
)
def test_extract_values_is_linear_on_adversarial_files(shape: str) -> None:
    text = shape * (300_000 // len(shape))
    for name in (".env", "secrets.json", "secrets.yml", ".pgpass", "master.key", ".npmrc"):
        started = time.perf_counter()
        extract_values(name, text)
        assert time.perf_counter() - started < 3, (name, shape)


@pytest.mark.parametrize("shape", ["$(", "a|", ";", "a=b\n", "k: |\n  x\n", "rm -rf x; ", "'"])
def test_classifying_a_huge_command_is_fast(shape: str) -> None:
    """Regression: the reasons tuple was copied per segment, so 200 KB of `a|a|...` took 21 s."""
    started = time.perf_counter()
    classify_command(shape * (200_000 // len(shape)))
    assert time.perf_counter() - started < 3


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (".env", "SECRET_KEY=secret\nDB_PASSWORD=password\nAPI_TOKEN=test\nCOOKIE_SECURE=true\nSESSION_TTL=3600\n"),
        (".env.example", "SECRET_KEY=changeme\nDB_PASSWORD=your-password-here\nAPI_TOKEN=xxxxxxxx\n"),
        (".env.sample", "API_TOKEN=<your token>\nSECRET=${SECRET}\n"),
    ],
)  # fmt: skip
def test_placeholders_and_template_files_do_not_become_masks(name: str, text: str) -> None:
    """Masking ordinary words ('secret', 'true', 'test') would make source and test output unreadable."""
    assert extract_values(name, text) == set()


def test_a_real_looking_value_in_a_template_file_is_still_learned() -> None:
    """Someone pasting a real key into .env.example is a leak waiting to happen: keep masking it."""
    got = extract_values(".env.example", "SECRET_KEY=Zq9xKp2LmN8vTr4Wb7Yc\n")
    assert "Zq9xKp2LmN8vTr4Wb7Yc" in got
