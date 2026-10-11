"""Natural language to the search grammar: the interface, and a deterministic fake (plan D11).

A compiler returns a **grammar string**, never SQL and never an AST with hidden fields. The string
then goes through the same parser and the same permission gate as a typed query (spec §77.2), so
"touching src/auth" becomes `file:src/auth` and is refused for an actor without `payload.read`
exactly as if they had typed it. Nothing here is wired to an endpoint, a setting or a vendor.
"""

import re
from collections.abc import Mapping
from typing import Protocol

from abb_api.search.fields import Field

FieldCatalogue = Mapping[str, Field]

__all__ = ["FieldCatalogue", "QueryCompiler", "RuleBasedCompiler"]

_MAX_INPUT = 512
_WORD = r"[\w./*-]{1,128}"
_NUMBER = r"\$?([0-9]{1,9}(?:\.[0-9]{1,6})?)"


class QueryCompiler(Protocol):
    def compile(self, text: str, *, fields: FieldCatalogue) -> str:
        """The grammar string for `text`, using only fields in `fields`."""
        ...


def _quote(value: str) -> str:
    return value if re.fullmatch(r"[\w./*-]+", value) else '"' + value.replace('"', "") + '"'


# (pattern, field, template). First match of each rule contributes one term, in table order.
_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bfailed\b|\bfailing\b|\bfailures?\b", "status", "status:failed"),
    (r"\bsucceeded\b|\bsuccessful\b", "status", "status:success"),
    (r"\btimed out\b", "status", "status:timed_out"),
    (r"\bwith errors?\b", "errors", "errors:>0"),
    (
        r"\b(?:more|greater) than " + _NUMBER + r"(?: dollars| usd)?\b(?! times)",
        "cost",
        "cost:>{0}",
    ),
    (r"\bcost(?:ing)? (?:over|above) " + _NUMBER, "cost", "cost:>{0}"),
    (r"\bretried more than ([0-9]{1,6}) times?\b", "retries", "retries:>{0}"),
    (r"\blast week\b", "started", "started:>=-7d"),
    (r"\b(?:today|last 24 hours|last day)\b", "started", "started:>=-24h"),
    (r"\btouching (" + _WORD + ")", "file", "file:{0}"),
    (r"\bthat (?:ran|executed) (" + _WORD + ")", "command", "command:{0}"),
    (r"\busing tool (" + _WORD + ")", "tool", "tool:{0}"),
    (r"\busing model (" + _WORD + ")", "model", "model:{0}"),
    (r"\bwith security events?\b", "security", "security:true"),
)


class RuleBasedCompiler:
    """A small phrase table: deterministic, dependency-free, enough to exercise the contract."""

    def compile(self, text: str, *, fields: FieldCatalogue) -> str:
        lowered = text[:_MAX_INPUT].lower()
        terms: list[str] = []
        taken: list[tuple[int, int]] = []
        for pattern, field, template in _RULES:
            if field not in fields:
                continue
            found = re.search(pattern, lowered)
            if found is None or any(found.start() < e and s < found.end() for s, e in taken):
                continue
            taken.append((found.start(), found.end()))
            values = [_quote(g) for g in found.groups() if g is not None]
            terms.append(template.format(*values))
        return " ".join(terms)
