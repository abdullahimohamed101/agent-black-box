"""Noise normalisation for step names (spec §98): ids, timestamps and temp paths become `#`.

Table-driven: `RULES` is an ordered list of (name, pattern, replacement). Only the content
projection ever calls this; the metadata projection never has a name to normalise. Every rule is a
linear-time pattern over a bounded string (`MAX_NAME` characters), so a hostile command cannot make
a comparison expensive.
"""

import re
from collections.abc import Callable, Sequence

MAX_NAME = 2_048
PLACEHOLDER = "#"
TMP_ROOT = "/tmp"  # noqa: S108 - the name every scratch directory is rewritten to
_ALNUM = r"(?<![0-9A-Za-z])"
_END = r"(?![0-9A-Za-z])"
_ULID = r"[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}"


def _hex(match: re.Match[str]) -> str:
    """A hex run of 8 or more characters. An all-letter run (`deadbeef`, `defaced`) is a word, not
    an id, unless a `0x` or a `_`, `-` or `/` boundary says it is a value."""
    text = match.string
    start = match.start()
    word = match.group(0)
    if re.fullmatch(r"[a-fA-F]+", word) and not (start > 0 and text[start - 1] in "_-/"):
        return word
    return PLACEHOLDER


RULES: tuple[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # Temp and scratch directories: the random directory name goes, the rest of the path stays.
    ("tmp_dir", re.compile(r"(?:/private)?/(?:var/folders/\w+/[\w.]+/T|tmp)/[^/\s]+"), TMP_ROOT),
    (
        "uuid",
        re.compile(
            _ALNUM
            + r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            + _END
        ),
        PLACEHOLDER,
    ),
    ("prefixed_ulid", re.compile(_ALNUM + r"[a-z]{2,6}_" + _ULID + _END), PLACEHOLDER),
    ("ulid", re.compile(_ALNUM + _ULID + _END), PLACEHOLDER),
    (
        "iso_timestamp",
        re.compile(
            _ALNUM
            + r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
            + _END
        ),
        PLACEHOLDER,
    ),
    ("clock", re.compile(_ALNUM + r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?" + _END), PLACEHOLDER),
    ("duration", re.compile(_ALNUM + r"\d+(?:\.\d+)?(?:ms|s|m|h)" + _END), PLACEHOLDER),
    ("hex_0x", re.compile(_ALNUM + r"0x[0-9a-fA-F]{8,}" + _END), PLACEHOLDER),
    ("hex", re.compile(_ALNUM + r"[0-9a-fA-F]{8,}" + _END), _hex),
    ("port", re.compile(r"(?<=:)\d{2,5}" + _END), PLACEHOLDER),
    ("digits", re.compile(_ALNUM + r"\d{4,}" + _END), PLACEHOLDER),
)
_SPACE = re.compile(r"\s+")


def strip_workdir(text: str, prefixes: Sequence[str]) -> str:
    """Remove the run's working directories where they start a path, and the scratch directory
    names: `/work/abc/app/x.py` and `/work/def/app/x.py` become the same relative-looking path."""
    out = _SPACE.sub(" ", text[:MAX_NAME]).strip()
    for prefix in sorted({p for p in prefixes if len(p) > 1}, key=len, reverse=True):
        out = re.sub(r"(?:(?<=\s)|^)" + re.escape(prefix.rstrip("/")) + r"(?=/)", "", out)
    _name, pattern, replacement = RULES[0]
    return pattern.sub(replacement, out)


def normalise(text: str, prefixes: Sequence[str] = ()) -> str:
    """The comparison form of a step name.

    `prefixes` are working directories of the run (from `shell.cwd`).
    """
    out = strip_workdir(text, prefixes)
    for _name, pattern, replacement in RULES[1:]:
        out = pattern.sub(replacement, out)
    return out.lower()
