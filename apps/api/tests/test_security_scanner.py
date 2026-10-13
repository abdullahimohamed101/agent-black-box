"""The server-side secret scanner (ADR-130): kinds, near misses, determinism, bounds, hostile input.

The timing tests use generous wall-clock bounds (seconds, for inputs that would take minutes or
hours with a backtracking pattern), so they stay stable on a slow runner while still failing for
an accidental quadratic pattern.
"""

import time
from collections.abc import Callable

import pytest

from abb_api.security.scanner import (
    HIGH_CONFIDENCE_KINDS,
    KINDS,
    PATTERN_KINDS,
    WINDOW,
    ScanBudget,
    ScanFailed,
    ScanTooLarge,
    SecretScanner,
    normalize,
)

SCANNER = SecretScanner()
AWS = "AKIAIOSFODNN7EXAMPLE"  # the documented example key, not a real credential
GH = "ghp_" + "a1B2c3D4e5" * 4
JWT_SIG = "dBjftJeZ4CVPmB92K27uhbUJU1p1r"
PEM_BODY = "MIIEowIBAAKCAQEAplantedsentinelbody0123456789"
PEM = f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----"


def scan(text: str, max_chars: int = 10**7, seconds: float = 30.0):  # type: ignore[no-untyped-def]
    return SCANNER.scan_text(text, ScanBudget(max_chars, seconds), unit_limit=10**9)


# (kind, text containing one secret, the secret part that must be gone)
CASES = [
    ("aws_access_key", f"key {AWS} end", AWS),
    ("aws_access_key", "temp ASIAIOSFODNN7EXAMPLE end", "ASIAIOSFODNN7EXAMPLE"),
    ("github_token", f"token {GH}", GH),
    ("github_token", "github_pat_" + "A1b2C3d4E5" * 3, "A1b2C3d4E5A1b2C3d4E5"),
    ("npm_token", "npm_" + "a1B2c3D4e5" * 3 + "a1B2c3", "a1B2c3D4e5a1B2c3D4e5"),
    ("gitlab_token", "glpat-" + "a1B2c3D4e5" * 2, "a1B2c3D4e5a1B2c3D4e5"),
    ("google_api_key", "AIza" + "a1B2c3D4e5" * 3 + "a1B2c", "a1B2c3D4e5a1B2c3D4e5"),
    ("google_oauth", "ya29." + "a1B2c3D4e5" * 3, "a1B2c3D4e5a1B2c3D4e5"),
    ("slack_webhook", "https://hooks.slack.com/services/T0/B0/XXXXsecretXXXX", "XXXXsecretXXXX"),
    ("slack_token", "xoxb-1234567890-abcdefghij", "1234567890-abcdefghij"),
    ("api_key", "sk-ant-" + "a1B2c3D4e5" * 3, "a1B2c3D4e5a1B2c3D4e5"),
    ("api_key", "sk_live_" + "a1B2c3D4e5" * 2, "a1B2c3D4e5a1B2c3D4e5"),
    ("abb_api_key", "abb_live_" + "a1B2c3D4e5" * 2, "a1B2c3D4e5a1B2c3D4e5"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + JWT_SIG, JWT_SIG),
    ("bearer_token", "Authorization: Bearer abcdefghij0123456789", "abcdefghij0123456789"),
    ("basic_auth", "curl -H 'Basic dXNlcjpwYXNzd29yZDEyMw=='", "dXNlcjpwYXNzd29yZDEyMw"),
    ("cli_user", "curl -u admin:s3cr3tpass https://x.example", "admin:s3cr3tpass"),
    ("cli_secret_flag", "tool --password 'p4ss w0rd' run", "p4ss w0rd"),
    ("quoted_key_value", '{"client_secret": "zzTOPsecretvalue"}', "zzTOPsecretvalue"),
    ("db_cli_password", "mysql -u root -phunter2 db", "hunter2"),
    ("npm_config_token", "//registry.npmjs.org/:_authToken=npmSecretValue123", "npmSecretValue123"),
    ("signed_url_param", "https://b.s3.example/o?X-Amz-Signature=abcdef01234567", "abcdef01234567"),
    ("cookie_header", "Cookie: session=abc123def456", "abc123def456"),
    ("url_credentials", "postgres://app:Sup3rS3cret@db.internal:5432/x", "Sup3rS3cret"),
    ("url_token_user", "https://ghp0123456789abcdefXYZ@github.com/o/r", "ghp0123456789abcdefXYZ"),
    ("secret_assignment", "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7M", "wJalrXUtnFEMIK7M"),
    ("secret_assignment_short", "DB_PASS=correct-horse-battery", "correct-horse-battery"),
    ("secret_comparison_literal", 'if password == "swordfish99":', "swordfish99"),
    ("private_key", f"before\n{PEM}\nafter", PEM_BODY),
    ("authorization", "authorization: Digest realm0987654321", "realm0987654321"),
]  # fmt: skip


def test_the_cases_cover_every_pattern_kind() -> None:
    # `credential` is the SDK's last-resort pattern: every text it would match is caught earlier
    # by `secret_assignment` or `secret_assignment_short`, so it cannot be the reported kind. It is
    # kept for parity with the SDK (tests/test_security_scanner_parity.py).
    assert {kind for kind, _, _ in CASES} == PATTERN_KINDS - {"credential"}


@pytest.mark.parametrize(
    ("kind", "text", "secret"), CASES, ids=[f"{k}-{i}" for i, (k, _, _) in enumerate(CASES)]
)
def test_a_secret_of_each_kind_is_replaced_and_named(kind: str, text: str, secret: str) -> None:
    result = scan(text)
    assert secret not in result.text
    assert "[REDACTED" in result.text
    assert kind in result.kinds
    assert result.count >= 1
    assert set(result.kinds) <= KINDS


@pytest.mark.parametrize(
    ("kind", "text", "secret"), CASES, ids=[f"{k}-{i}" for i, (k, _, _) in enumerate(CASES)]
)
def test_scanning_redacted_text_changes_nothing_and_counts_nothing(
    kind: str, text: str, secret: str
) -> None:
    """Idempotence: what the SDK (or an earlier pass) redacted is not a finding again."""
    once = scan(text)
    twice = scan(once.text)
    assert twice.text == once.text
    assert twice.count == 0 and twice.kinds == ()


def test_the_same_input_always_gives_the_same_result() -> None:
    text = "\n".join(t for _, t, _ in CASES)
    first, second = scan(text), scan(text)
    assert first == second
    assert list(first.kinds) == sorted(first.kinds)


def test_the_result_never_contains_the_secret_in_kinds_or_text() -> None:
    result = scan(f"a {AWS} b {GH} c\n{PEM}")
    flat = result.text + "".join(result.kinds)
    for secret in (AWS, GH, PEM_BODY):
        assert secret not in flat
    assert result.count == 3


@pytest.mark.parametrize(
    "text",
    [
        "The quick brown fox jumps over the lazy dog.",
        "AKIA is a prefix and AKIAshort stays; so does ASIA-1234",
        "ghp_short and gho_ tokens must be long: ghp_abc123",
        "sk-short and risk-assessment-and-task-list-and-more-words are prose",
        "see https://example.com/docs?page=2&sort=name for more",
        "password policy: at least 12 characters",
        "the token count was 4096 and the key was 'name'",
        "total_tokens = 1500 and max_tokens=4096",
        "a = b == c, x != y",
        "git commit -m 'fix: handle keys'",
        "eyJ not a jwt, and neither is eyJabc.def.ghi",
        "Bearer token in prose, bearer short",
        "https://user@example.com/ and ssh://git@github.com/o/r",
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
        "rm -rf build && npm test -- --coverage",
    ],
)
def test_near_misses_and_ordinary_text_are_untouched(text: str) -> None:
    result = scan(text)
    assert result.text == text
    assert result.count == 0 and result.kinds == ()


def test_unicode_around_a_secret_is_kept() -> None:
    result = scan(f"ключ 鍵 🔑 {AWS} → fin é\nline two ñ")
    assert result.text == "ключ 鍵 🔑 [REDACTED:aws_access_key] → fin é\nline two ñ"


def test_nul_and_terminal_escapes_cannot_split_a_secret() -> None:
    split = f"{AWS[:6]}\x00{AWS[6:]} and {GH[:8]}\x1b[0m{GH[8:]}"
    assert AWS in normalize(split)  # the escape and NUL are removed, so the key is whole again
    result = scan(split)
    assert result.text == "[REDACTED:aws_access_key] and [REDACTED:github_token]"


def test_a_private_key_without_an_end_marker_is_redacted_to_the_end() -> None:
    result = scan(f"head\n-----BEGIN OPENSSH PRIVATE KEY-----\n{PEM_BODY}\nmore lines\n")
    assert result.text == "head\n[REDACTED:private_key]"


def test_two_private_keys_and_text_between_them() -> None:
    result = scan(f"{PEM}\nkeep me\n{PEM}\ntail")
    assert result.text == "[REDACTED:private_key]\nkeep me\n[REDACTED:private_key]\ntail"
    assert result.count == 2


def test_multi_line_documents_are_scanned_in_windows() -> None:
    lines = [f"line {i} nothing here" for i in range(20000)]
    lines[12345] = f"found {AWS} here"
    result = scan("\n".join(lines))
    assert result.count == 1
    assert AWS not in result.text
    assert result.text.count("\n") == 19999


# ---------------------------------------------------------------------------- budgets


def test_beyond_the_size_budget_the_scan_raises_too_large() -> None:
    with pytest.raises(ScanTooLarge):
        scan("x" * 1001, max_chars=1000)


def test_the_budget_is_shared_by_several_scans() -> None:
    budget = ScanBudget(1500)
    SCANNER.scan_text("y" * 1000, budget)
    with pytest.raises(ScanTooLarge):
        SCANNER.scan_text("y" * 1000, budget)


def test_a_value_over_the_unit_limit_is_too_large_whatever_the_budget() -> None:
    with pytest.raises(ScanTooLarge):
        SCANNER.scan_text("z" * 101, ScanBudget(10**9), unit_limit=100)


def test_an_expired_time_budget_raises_scan_failed() -> None:
    ticks = iter([0.0, 5.0, 5.0, 5.0, 5.0])
    budget = ScanBudget(10**6, 1.0, clock=lambda: next(ticks))
    with pytest.raises(ScanFailed):
        SCANNER.scan_text("some text", budget)


def test_a_line_that_cannot_be_split_is_too_large_not_stored_unscanned() -> None:
    with pytest.raises(ScanTooLarge):
        scan("a" * (WINDOW + 10))


def test_a_long_line_with_separators_is_split_and_scanned() -> None:
    text = ("word " * 30000) + AWS + (" more" * 10)
    result = scan(text)
    assert AWS not in result.text and result.count == 1
    assert len(result.text) == len(text) - len(AWS) + len("[REDACTED:aws_access_key]")


def test_an_unexpected_error_inside_the_scan_becomes_scan_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: object) -> str:
        raise RuntimeError("secret AKIAIOSFODNN7EXAMPLE in the message")

    monkeypatch.setattr("abb_api.security.scanner._scan_window", boom)
    with pytest.raises(ScanFailed) as caught:
        scan("password=hunter2hunter2")
    assert "AKIA" not in str(caught.value)  # the original message is not carried


# ---------------------------------------------------------------------------- bytes


def test_bytes_round_trip_invalid_utf8_unchanged() -> None:
    data = b"\xff\xfe binary \x80 then " + AWS.encode() + b" and \xc3\x28 end"
    result = SCANNER.scan_bytes(data, ScanBudget(10**6))
    assert AWS.encode() not in result.data
    assert result.data == b"\xff\xfe binary \x80 then [REDACTED:aws_access_key] and \xc3\x28 end"


def test_bytes_without_secrets_are_returned_identical() -> None:
    data = ("hello ünïcode\n" * 1000).encode()
    result = SCANNER.scan_bytes(data, ScanBudget(10**6))
    assert result.data == data and result.count == 0


def test_bytes_over_budget_are_never_decoded() -> None:
    with pytest.raises(ScanTooLarge):
        SCANNER.scan_bytes(b"x" * 100, ScanBudget(50))


# ------------------------------------------------------------- hostile input, time bounds

N = 1_000_000


def spaced(unit: str, every: int = 20_000) -> str:
    """`unit` repeated to N characters with a space now and then, so the line can be split."""
    block = unit * max(1, every // len(unit)) + " "
    return block * (N // len(block) + 1)


ADVERSARIAL: list[tuple[str, Callable[[], str]]] = [
    ("jwt-lookalikes", lambda: spaced("eyJ-")),
    ("jwt-two-dots", lambda: spaced("eyJaaaaaaaa.bbbbbbbb")),
    ("quote-and-keywords", lambda: spaced("'key")),
    ("keyword-run", lambda: spaced("secret")),
    ("assignment-names", lambda: spaced("pass_")),
    ("assignment-long-names", lambda: spaced("A_")),
    ("mysql-no-flag", lambda: spaced("mysql ")),
    ("url-colons", lambda: spaced("://a:")),
    ("url-slashes", lambda: spaced("//")),
    ("npm-config", lambda: spaced("npm config set a")),
    ("flags-with-open-quotes", lambda: spaced(' --password "')),
    ("authorization-spam", lambda: spaced('authorization:"')),
    ("password-quote", lambda: spaced('password="')),
    ("bearer-spaces", lambda: spaced("bearer ")),
    ("sk-prefixes", lambda: spaced("sk-")),
    ("assignments", lambda: spaced("k=")),
    ("begin-markers", lambda: spaced("-----BEGIN PRIVATE KEY----- ")),
    ("end-markers-only", lambda: spaced("-----END PRIVATE KEY----- ")),
    ("ansi-escapes", lambda: spaced("\x1b[31m")),
    ("hex", lambda: spaced("0123456789abcdef")),
    ("newlines", lambda: "\n" * N),
    ("prose", lambda: ("The quick brown fox jumps. " * 40 + "\n") * (N // 1100)),
]


@pytest.mark.parametrize(("name", "build"), ADVERSARIAL, ids=[n for n, _ in ADVERSARIAL])
def test_hostile_input_of_a_megabyte_scans_in_bounded_time(
    name: str, build: Callable[[], str]
) -> None:
    text = build()
    started = time.perf_counter()
    try:
        result = scan(text)
    except ScanTooLarge:
        # A line with no cut that keeps every secret whole is refused (fail closed, review P3-1):
        # the contract here is a bounded answer, either a scan or a refusal.
        result = None
    elapsed = time.perf_counter() - started
    assert elapsed < 8.0, f"{name}: {elapsed:.1f}s for {len(text)} characters"
    assert result is None or isinstance(result.text, str)


def test_the_time_budget_stops_a_slow_scan_instead_of_hanging() -> None:
    text = spaced('password="')
    with pytest.raises(ScanFailed):
        SCANNER.scan_text(text, ScanBudget(10**8, 0.001), unit_limit=10**9)


# ------------------------------------------------------------------------ the needle gates


def test_the_per_pattern_gates_never_change_a_result() -> None:
    """A gate is only a shortcut: with and without it every text gives the same output."""
    from abb_api.security.scanner import _scan_window, _Tally

    samples = [text for _, text, _ in CASES] + [
        "TOKEN=abc123456 and Cookie: a=b; PASSWORD='x y'",
        "Authorization:Digest abcdef; BASIC dXNlcjpwYXNzd29yZDEyMw== ",
        "see ://u:pw@host and --Password=Hunter22 -U bob:pw",
        "MYSQL -pSECRET; DOCKER LOGIN -p s3cr3t",
        "ünïcode ARTICLE bearer abcdefghij0123456789 \u0131nput author\u0131zation: abcdefghi",
        "if PASSWORD == 'abc': pass",
        "x" * 100,
    ]
    for text in samples:
        gated_tally, plain_tally = _Tally(), _Tally()
        assert _scan_window(text, gated_tally) == _scan_window(text, plain_tally, gated=False)
        assert gated_tally.count == plain_tally.count and gated_tally.kinds == plain_tally.kinds


def test_high_confidence_kinds_are_real_kinds_found_by_their_own_sample() -> None:
    assert HIGH_CONFIDENCE_KINDS <= PATTERN_KINDS
    found = {kind for kind, text, _ in CASES if kind in HIGH_CONFIDENCE_KINDS}
    assert found == HIGH_CONFIDENCE_KINDS
    for kind, text, _ in CASES:
        if kind in HIGH_CONFIDENCE_KINDS:
            assert scan(text).kinds == (kind,)  # nothing else fires on a provider-shaped token


# ---------------------------------- ordinary content is stored verbatim (review P1-1, P2-1)

# `-u` / `-U` is a flag of most programs and only a credential for a few: ordinary commands are
# the text an engineer reads back in the run page, so the scanner must not rewrite them.
ORDINARY_COMMANDS = [
    "python -u main.py",
    "python3 -u -m pytest tests/",
    "pip install -U requests",
    "pip install --upgrade -U pip",
    "sort -u names.txt",
    "sort -u .env",
    "uniq -u words.txt",
    "head -u file",
    "docker run -u 1000 image",
    "docker run --user 1000:1000 image",
    "docker run --user root image",
    "useradd -u 1000 bob",
    "psql -U postgres -d app",
    "git push -u origin feature/x",
    "sudo -u deploy ./restart.sh",
    "ssh -l alice host",
]


@pytest.mark.parametrize("command", ORDINARY_COMMANDS)
def test_ordinary_commands_with_a_u_flag_are_stored_verbatim(command: str) -> None:
    result = scan(command)
    assert result.text == command
    assert result.count == 0 and result.kinds == ()


@pytest.mark.parametrize(
    ("command", "secret"),
    [
        ("curl -u alice:s3cret https://x.example", "s3cret"),
        ("curl -u 'alice:s3cret pw' https://x.example", "s3cret"),
        ("curl --user alice:s3cret https://x.example", "s3cret"),
        ("curl --user=alice:s3cret https://x.example", "s3cret"),
        ("curl --proxy-user proxy:s3cret https://x.example", "s3cret"),
        ("curl -U alice:s3cret https://x.example", "s3cret"),
        ("wget -u user:pass https://x.example", "user:pass"),
        ("docker login -u me -p s3cret registry.example.com", "s3cret"),
        ("mysql -u root -phunter2 db", "hunter2"),
        ("mysql -u root -p'hunter2' db", "hunter2"),
    ],
)
def test_a_user_password_credential_argument_is_still_redacted(command: str, secret: str) -> None:
    result = scan(command)
    assert secret not in result.text
    assert "[REDACTED" in result.text


def test_a_u_flag_does_not_blind_the_secret_file_detector() -> None:
    """`sort -u .env` must reach the detectors with the file name intact."""
    from abb_api.security import rules

    command = scan("sort -u .env").text
    assert command == "sort -u .env"
    assert rules.reads_secret_file(command)


BENIGN_ASSIGNMENTS = [
    "PWD=/home/alice",
    "OLDPWD=/home/alice/work",
    "pwd: /tmp/x",
    "cd $PWD && ls",
    "MONKEY=banana",
    "primary_key: id",
    "primaryKey = 1",
    "cache_key = f'{a}:{b}'",
    "idempotency_key: abc-123",
    "sort_key=name",
    "partition_key: user_id",
    "foreign_key=owner_id",
    "ssh_key: ~/.ssh/id_rsa",
    "public_key: ssh-ed25519-example",
    "token_count=5",
    "max_tokens=4096",
    "token_type == 'bearer'",
    "password_min_length: 12",
    "secret_name: db-creds",
    "passed=5 failed=0",
    "bypass=1 compass=north",
    "keyboard: us",
    "author: Ada Lovelace",
    '{"path": "/tmp/x", "author": "bob", "primary_key": "id", "passed": true}',
    "{'monkey': 'banana', 'cache_key': 'k1'}",
    "https://my-long-service-account@git.example.com/repo",
    "ssh://deployment-automation-bot@host.example.com/repo",
]


@pytest.mark.parametrize("text", BENIGN_ASSIGNMENTS)
def test_assignments_that_are_not_credentials_are_stored_verbatim(text: str) -> None:
    result = scan(text)
    assert result.text == text
    assert result.count == 0 and result.kinds == ()


# (text, the secret value that must be gone). The key's last name segment is a secret word, or a
# qualified `key` (api/access/private/secret/auth/... `_key`).
REAL_ASSIGNMENTS = [
    ("DB_PASSWORD='p4ss w0rd'", "p4ss w0rd"),
    ("MYSQL_ROOT_PASSWORD=toor1234", "toor1234"),
    ("password: hunter2", "hunter2"),
    ("passwd = hunter2", "hunter2"),
    ("dbpassword=hunter2", "hunter2"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI", "wJalrXUtnFEMI"),
    ("STRIPE_SECRET_KEY=zzTOP1234", "zzTOP1234"),
    ("SECRET_KEY=django-insecure-abc", "django-insecure-abc"),
    ("OPENAI_API_KEY=zzTOP1234", "zzTOP1234"),
    ("apiKey: zzTOP1234", "zzTOP1234"),
    ("api-key = zzTOP1234", "zzTOP1234"),
    ("x-api-key: zzTOP1234", "zzTOP1234"),
    ("ACCESS_KEY=zzTOP1234", "zzTOP1234"),
    ("private_key = zzTOP1234", "zzTOP1234"),
    ("ENCRYPTION_KEY=zzTOP1234", "zzTOP1234"),
    ("clientSecret = 'zzTOP1234'", "zzTOP1234"),
    ("client_secret=zzTOP1234", "zzTOP1234"),
    ("GITHUB_TOKEN=zzTOP1234", "zzTOP1234"),
    ("accessToken: zzTOP1234", "zzTOP1234"),
    ("token: zzTOP1234", "zzTOP1234"),
    ("SENTRY_DSN=https://abc@o1.ingest.example/1", "https://abc@o1.ingest.example/1"),
    ("DB_PASS=hunter2", "hunter2"),
    ("pass=hunter2", "hunter2"),
    ("GH_PAT=zzTOP1234", "zzTOP1234"),
    ("ROOT_PWD=toor1234", "toor1234"),
    ("ghPat: zzTOP1234", "zzTOP1234"),
    ("AWS_CREDENTIALS=zzTOP1234", "zzTOP1234"),
    ('if password == "swordfish99":', "swordfish99"),
    ("if (clientSecret === 'swordfish99')", "swordfish99"),
    ('{"password": "x y z"}', "x y z"),
    ('{"client_secret": "zzTOP1234"}', "zzTOP1234"),
    ('{"apiKey": "zzTOP1234"}', "zzTOP1234"),
    ('{"access_token": "zzTOP1234"}', "zzTOP1234"),
    ('{"private_key": "zzTOP1234"}', "zzTOP1234"),
    ('{"auth": "zzTOP1234"}', "zzTOP1234"),
    ('{"Authorization": "Custom zzTOP1234"}', "zzTOP1234"),
    ('{"cookie": "sid=zzTOP1234"}', "zzTOP1234"),
    ("{'db_password': 'zzTOP1234'}", "zzTOP1234"),
    ('{"gh_pat": "zzTOP1234"}', "zzTOP1234"),
]


@pytest.mark.parametrize(("text", "secret"), REAL_ASSIGNMENTS)
def test_assignments_of_real_secrets_are_still_redacted(text: str, secret: str) -> None:
    result = scan(text)
    assert secret not in result.text
    assert "[REDACTED" in result.text
    assert result.count >= 1


def test_a_long_username_without_a_password_is_not_a_credential() -> None:
    for text in (
        "https://my-long-service-account@git.example.com/repo",
        "git clone https://first.last.engineer@bitbucket.example.com/r.git",
    ):
        assert scan(text).text == text


def test_a_long_token_shaped_user_is_still_redacted() -> None:
    result = scan("https://ghp0123456789abcdefXYZ@github.com/o/r")
    assert "ghp0123456789abcdefXYZ" not in result.text


@pytest.mark.parametrize(
    "text",
    [
        '{"password": "[REDACTED]"}',
        "password=\"[REDACTED]\" api_key='[REDACTED:credential]'",
        'DB_PASSWORD="[REDACTED:credential]"',
    ],
)
def test_a_quoted_marker_is_not_redacted_again_or_counted(text: str) -> None:
    result = scan(text)
    assert result.text == text
    assert result.count == 0 and result.kinds == ()


# ------------------------------------------------- a window cut never splits a secret (review P3-1)


def _line_cut_after(prefix: str, rest: str) -> str:
    """One line (no newline) whose first window ends right after `prefix`."""
    room = WINDOW - len(prefix)
    return "word " * (room // 5) + " " * (room % 5) + prefix + rest + " tail" * 20


@pytest.mark.parametrize(
    ("prefix", "rest", "secrets"),
    [
        ('password = "abc ', 'def ghi" end', ("abc", "def", "ghi")),
        ("DB_PASSWORD='one ", "two three' end", ("one", "two", "three")),
        ("Authorization: Bearer ", "abcdefghij0123456789 end", ("abcdefghij0123456789",)),
        ("mysql -u root -p", "hunter2secret db", ("hunter2secret",)),
        ("Cookie: a=b; ", "session=zz9plural; c=d", ("zz9plural",)),
    ],
)
def test_a_secret_that_straddles_a_window_cut_is_still_redacted(
    prefix: str, rest: str, secrets: tuple[str, ...]
) -> None:
    result = scan(_line_cut_after(prefix, rest))
    for secret in secrets:
        assert secret not in result.text


def test_a_cut_that_cannot_be_made_safe_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("abb_api.security.scanner._seam_is_safe", lambda text, cut: False)
    with pytest.raises(ScanTooLarge):
        scan("word " * (WINDOW // 5 + 1000))


def test_a_cut_is_moved_to_the_nearest_safe_break() -> None:
    from abb_api.security.scanner import _seam_is_safe

    text = 'password = "abc def ghi" ' + "x " * 10
    cut_inside = text.index("def")  # between `abc ` and `def`
    assert not _seam_is_safe(text, cut_inside)
    assert _seam_is_safe(text, text.index("x"))


# ------------------------------------- look-alike and zero-width characters (review P3-6)

FULLWIDTH_AWS = "".join(chr(ord(c) + 0xFEE0) for c in AWS)  # the same key in full-width letters


@pytest.mark.parametrize(
    "obfuscated",
    [
        AWS[:4] + "\u200b" + AWS[4:],  # zero-width space
        AWS[:6] + "\u2060" + AWS[6:],  # word joiner
        AWS[:3] + "\ufeff" + AWS[3:],  # byte-order mark inside the key
        FULLWIDTH_AWS,
    ],
    ids=["zero-width-space", "word-joiner", "bom", "full-width"],
)
def test_a_secret_hidden_by_look_alike_characters_is_redacted_line_wise(obfuscated: str) -> None:
    text = f"first line é\nkey {obfuscated} end\nlast line ñ"
    result = scan(text)
    assert obfuscated not in result.text and AWS not in result.text
    assert result.text.split("\n")[0] == "first line é"  # other lines are stored as they were
    assert result.text.split("\n")[2] == "last line ñ"
    assert "[REDACTED:aws_access_key]" in result.text
    assert result.kinds == ("aws_access_key",) and result.count == 1
    again = scan(result.text)
    assert again.text == result.text and again.count == 0


@pytest.mark.parametrize(
    "text",
    [
        "\uff21\uff22\uff23\u3000\uff54\uff45\uff53\uff54 \uff11\uff12\uff13",  # full-width
        "zero\u200bwidth in prose\u200d is kept",
        "日本語のテキスト、\uff21\uff2b\uff29\uff21 is short",
        "ﬁle ﬂow ① ② ㎏",
    ],
)
def test_non_ascii_text_that_is_not_a_secret_is_stored_as_it_was(text: str) -> None:
    result = scan(text)
    assert result.text == text and result.count == 0
