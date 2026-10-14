"""Resource globs: the documented semantics, an independent regex oracle on random inputs, and
adversarial inputs against a time bound (no backtracking, ADR-140)."""

import random
import re
import time

import pytest

from abb_api.policies.glob import MAX_TEXT, compile_glob

CASES = [
    # (pattern, text, matches)
    ("**/.env*", ".env", True),
    ("**/.env*", ".env.local", True),
    ("**/.env*", "config/.env", True),
    ("**/.env*", "a/b/c/.env.production", True),
    ("**/.env*", "a/b/env", False),
    ("**/.env*", "a/.envrc/x", False),  # `*` stops at a slash
    ("**/.env*", "x.env", False),  # `**/` is whole directories, not a prefix of the name
    ("src/*", "src/main.py", True),
    ("src/*", "src/pkg/main.py", False),
    ("src/**", "src/pkg/main.py", True),
    ("src/**", "src/", True),
    ("src/**", "src", False),
    ("*.py", "main.py", True),
    ("*.py", "pkg/main.py", False),
    ("**.py", "pkg/main.py", True),
    ("file?.txt", "file1.txt", True),
    ("file?.txt", "file.txt", False),
    ("file?.txt", "file/.txt", False),  # `?` is never a slash
    ("exact", "exact", True),
    ("exact", "Exact", False),  # case sensitive
    ("exact", "exact/", False),
    ("a**b", "a/x/b", True),
    ("a*b", "a/x/b", False),
    ("**", "anything/at/all", True),
    ("**", "", True),
    ("*", "", True),
    ("*", "a/b", False),
    ("[a]{b}\\", "[a]{b}\\", True),  # no other special characters
    ("**/", "", True),
    ("**/", "a/", True),
    ("**/", "a", False),
    ("repo:org/*#pr-*", "repo:org/project#pr-188", True),
    ("日本*", "日本語", True),
]


@pytest.mark.parametrize(("pattern", "text", "expected"), CASES)
def test_documented_cases(pattern: str, text: str, expected: bool) -> None:
    assert compile_glob(pattern).matches(text) is expected


def oracle(pattern: str) -> re.Pattern[str]:
    """The same semantics through the standard regex engine (only used on tiny inputs)."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            j = i
            while j < len(pattern) and pattern[j] == "*":
                j += 1
            if j - i >= 2 and (i == 0 or pattern[i - 1] == "/") and pattern[j : j + 1] == "/":
                out.append("(?:[^/]*/)*")
                j += 1
            elif j - i >= 2:
                out.append(".*")
            else:
                out.append("[^/]*")
            i = j
        else:
            out.append("[^/]" if ch == "?" else re.escape(ch))
            i += 1
    return re.compile("".join(out), re.DOTALL)


def test_matches_an_independent_oracle_on_random_patterns_and_texts() -> None:
    rng = random.Random(1234)
    pieces = ["a", "b", "/", "*", "**", "**/", "?", ".", "a", "b"]
    for _ in range(3000):
        pattern = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 7)))
        compiled, reference = compile_glob(pattern), oracle(pattern)
        for _ in range(8):
            text = "".join(rng.choice("ab/.") for _ in range(rng.randint(0, 9)))
            assert compiled.matches(text) == bool(reference.fullmatch(text)), (pattern, text)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        ("*" * 200 + "b", "a" * 4000),
        ("*a" * 100 + "b", "a" * 4000),
        ("**/" * 60 + "x", "a/" * 2000),
        ("a*" * 100, "a" * 4000 + "b"),
        ("?*" * 100 + "z", "a" * 4000),
        ("*/" * 100 + "z", "a/" * 2000),
    ],
)
def test_adversarial_patterns_stay_fast(pattern: str, text: str) -> None:
    glob = compile_glob(pattern[:256])
    started = time.perf_counter()
    for _ in range(5):
        glob.matches(text)
    assert time.perf_counter() - started < 1.0  # five matches of the worst shapes


def test_a_text_over_the_cap_never_matches() -> None:
    assert not compile_glob("**").matches("a" * (MAX_TEXT + 1))
    assert compile_glob("**").matches("a" * MAX_TEXT)
