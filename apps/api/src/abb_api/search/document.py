"""The per-run search document: a pure function of the run's events (INV-2, plan D4).

Built by the summarizer from the events it already holds and written only when its hash changed.
Two halves, never mixed (ADR-101):

* **metadata**: event types, tool/model/provider names, agents, tags, run name, db/http operations,
  the `span.name` *attribute* (never the derived span name, which falls back to commands and paths)
  and string leaves of the run's client-supplied `metadata`;
* **content**: values the read projection withholds from actors without `payload.read`
  (`abb_api/content.py`): command lines, file paths, cwd, git and url values, and unregistered
  attributes whose name says they are content.

Unregistered attributes that are not content by name are not indexed at all (fail closed).
Everything is bounded: 2,000 distinct values and 64 KiB per array, 8,000 token positions per
document; exceeding a bound sets that half's own truncation flag.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from abb_event_schema.enums import EventClass
from abb_event_schema.event import Event
from abb_event_schema.ordering import sort_events
from abb_event_schema.registry import classify

from abb_api.content import is_content_attribute
from abb_api.search.tokens import normalise, positions
from abb_api.search.tsliteral import tsvector_literal

MAX_VALUES = 2_000
MAX_ARRAY_BYTES = 64 * 1024
MAX_POSITIONS = 8_000
MAX_VALUE_CHARS = 256  # a longer value could never be matched by a query value
MAX_CONTENT_VALUE_CHARS = 4_096  # the attribute string limit
_METADATA_LEAF_DEPTH = 4

# attribute -> array column (values are normalised: NFKC + case-fold)
_ARRAY_ATTRIBUTES = {"tool.name": "tools", "llm.model": "models", "llm.provider": "providers"}
# indexed as words only
_TEXT_ATTRIBUTES = ("tool.operation", "db.operation", "http.method", "span.name")


@dataclass(frozen=True)
class SearchDocument:
    error_count: int
    security_events: int
    event_types: tuple[str, ...]
    tools: tuple[str, ...]
    models: tuple[str, ...]
    providers: tuple[str, ...]
    agents: tuple[str, ...]
    tags: tuple[str, ...]
    metadata_doc: str  # tsvector literal
    metadata_truncated: bool
    file_paths: tuple[str, ...]
    commands: tuple[str, ...]
    content_doc: str  # tsvector literal
    content_truncated: bool

    @property
    def hash(self) -> bytes:
        canonical = json.dumps(
            {name: getattr(self, name) for name in self.__dataclass_fields__},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).digest()


class _Bounded:
    """Distinct values in first-seen order, up to the count and size bounds."""

    def __init__(self, *, max_chars: int) -> None:
        self.values: list[str] = []
        self.truncated = False
        self._seen: set[str] = set()
        self._bytes = 0
        self._max_chars = max_chars
        self._full = False

    def add(self, value: str) -> None:
        if not value or value in self._seen:
            return
        if len(value) > self._max_chars:
            self.truncated = True
            return
        size = len(value.encode())
        if self._full or len(self.values) >= MAX_VALUES or self._bytes + size > MAX_ARRAY_BYTES:
            self._full = self.truncated = True  # keep a prefix: no later value sneaks in
            return
        self._seen.add(value)
        self.values.append(value)
        self._bytes += size


def distinct_agents(events: Iterable[Event]) -> tuple[str, ...]:
    """The distinct agent ids of the events in canonical order (before multi-agent derivation)."""
    return tuple(dict.fromkeys(e.agent_id for e in sort_events(events)))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _leaves(value: Any, depth: int = _METADATA_LEAF_DEPTH) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif depth > 0 and isinstance(value, Mapping):
        for item in value.values():
            yield from _leaves(item, depth - 1)
    elif depth > 0 and isinstance(value, list):
        for item in value:
            yield from _leaves(item, depth - 1)


def build_document(
    events: Sequence[Event],
    *,
    run_name: str | None,
    run_metadata: Mapping[str, Any],
    agents: Sequence[str],
    error_count: int,
) -> SearchDocument:
    """The document for one run. `error_count` is the derivation's own figure (one rule only)."""
    ordered = sort_events(events)
    arrays = {
        name: _Bounded(max_chars=MAX_VALUE_CHARS)
        for name in ("event_types", "tools", "models", "providers", "agents", "tags")
    }
    words = _Bounded(max_chars=MAX_CONTENT_VALUE_CHARS)  # extra metadata words, one source
    commands = _Bounded(max_chars=MAX_CONTENT_VALUE_CHARS)
    paths = _Bounded(max_chars=MAX_CONTENT_VALUE_CHARS)
    content_words = _Bounded(max_chars=MAX_CONTENT_VALUE_CHARS)
    security = 0

    for agent in agents:
        arrays["agents"].add(normalise(agent))
    for event in ordered:
        arrays["event_types"].add(event.event_type)
        for tag in event.tags:
            arrays["tags"].add(normalise(tag))
        if classify(event.event_type) is EventClass.SECURITY:
            security += 1
        for key, value in event.attributes.items():
            if is_content_attribute(key):
                for text in _strings(value):
                    content_words.add(text)
                    if key == "shell.command":
                        commands.add(text)
                    elif key == "file.path":
                        paths.add(text)
            elif key in _ARRAY_ATTRIBUTES:
                for text in _strings(value):
                    arrays[_ARRAY_ATTRIBUTES[key]].add(normalise(text))
            elif key in _TEXT_ATTRIBUTES:
                for text in _strings(value):
                    words.add(text)
    if run_name:
        words.add(run_name)
    for leaf in _leaves(run_metadata):
        words.add(leaf)

    metadata_texts = [
        *([run_name] if run_name else []),
        *arrays["agents"].values,
        *arrays["event_types"].values,
        *arrays["tags"].values,
        *arrays["tools"].values,
        *arrays["models"].values,
        *arrays["providers"].values,
        *words.values,
    ]
    metadata_positions, metadata_cut = positions(metadata_texts, max_positions=MAX_POSITIONS)
    content_positions, content_cut = positions(content_words.values, max_positions=MAX_POSITIONS)
    metadata_truncated = (
        metadata_cut or words.truncated or any(a.truncated for a in arrays.values())
    )
    return SearchDocument(
        error_count=error_count,
        security_events=security,
        event_types=tuple(arrays["event_types"].values),
        tools=tuple(arrays["tools"].values),
        models=tuple(arrays["models"].values),
        providers=tuple(arrays["providers"].values),
        agents=tuple(arrays["agents"].values),
        tags=tuple(arrays["tags"].values),
        metadata_doc=tsvector_literal(metadata_positions),
        metadata_truncated=metadata_truncated,
        file_paths=tuple(paths.values),
        commands=tuple(commands.values),
        content_doc=tsvector_literal(content_positions),
        content_truncated=content_cut
        or content_words.truncated
        or paths.truncated
        or commands.truncated,
    )
