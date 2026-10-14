"""Resource glob matching for the cached fail-mode table (a copy of the server's matcher).

Identical to `abb_api.policies.glob` (ADR-140): `*` any run except `/`, `**` any run, `**/` zero or
more directories, `?` one character except `/`; whole-text, case sensitive, linear in the text and
never backtracking. A test loads the server's file and compares the two on many patterns, so the
SDK table and the server can never disagree about what a glob means.
"""

from dataclasses import dataclass

MAX_TEXT = 4096

# State kinds.
_LIT, _ANY1, _STAR, _GLOB, _DIR_A, _DIR_B = range(6)


@dataclass(frozen=True)
class Glob:
    """A compiled pattern. Build with `compile_glob`; match with `matches`."""

    source: str
    literal: str | None  # no wildcard at all: compare for equality
    _lit_masks: dict[str, int]
    _any1: int
    _star: int
    _glob: int
    _dir_a: int
    _dir_b: int
    _eps: tuple[tuple[int, int], ...]  # (state, target) epsilon edges, ascending by state
    _accept: int
    _start: int

    def matches(self, text: str) -> bool:
        if len(text) > MAX_TEXT:
            return False
        if self.literal is not None:
            return text == self.literal
        live = self._closure(self._start)
        for ch in text:
            slash = ch == "/"
            nxt = (live & self._lit_masks.get(ch, 0)) << 1
            nxt |= live & self._glob
            if slash:
                nxt |= live & self._dir_a
                nxt |= (live & self._dir_b) >> 1
            else:
                nxt |= (live & self._any1) << 1
                nxt |= live & self._star
                nxt |= (live & self._dir_a) << 1
                nxt |= live & self._dir_b
            if not nxt:
                return False
            live = self._closure(nxt)
        return bool(live & self._accept)

    def _closure(self, live: int) -> int:
        for state, target in self._eps:
            if live >> state & 1:
                live |= 1 << target
        return live


def _tokens(pattern: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            run = j - i
            at_boundary = i == 0 or pattern[i - 1] == "/"
            if run >= 2 and at_boundary and j < n and pattern[j] == "/":
                out.append((_DIR_A, ""))  # `**/`: zero or more directories
                j += 1
            elif run >= 2:
                out.append((_GLOB, ""))
            else:
                out.append((_STAR, ""))
            i = j
        elif ch == "?":
            out.append((_ANY1, ""))
            i += 1
        else:
            out.append((_LIT, ch))
            i += 1
    return out


def compile_glob(pattern: str) -> Glob:
    if not any(c in pattern for c in "*?"):
        return Glob(pattern, pattern, {}, 0, 0, 0, 0, 0, (), 0, 0)
    lit_masks: dict[str, int] = {}
    any1 = star = globstar = dir_a = dir_b = 0
    eps: list[tuple[int, int]] = []
    state = 0
    for kind, ch in _tokens(pattern):
        bit = 1 << state
        if kind == _LIT:
            lit_masks[ch] = lit_masks.get(ch, 0) | bit
            state += 1
        elif kind == _ANY1:
            any1 |= bit
            state += 1
        elif kind == _STAR:
            star |= bit
            eps.append((state, state + 1))
            state += 1
        elif kind == _GLOB:
            globstar |= bit
            eps.append((state, state + 1))
            state += 1
        else:  # _DIR_A occupies two states: at a segment start (a) and inside a segment (b)
            dir_a |= bit
            dir_b |= bit << 1
            eps.append((state, state + 2))
            state += 2
    return Glob(
        pattern, None, lit_masks, any1, star, globstar, dir_a, dir_b, tuple(eps), 1 << state, 1
    )
