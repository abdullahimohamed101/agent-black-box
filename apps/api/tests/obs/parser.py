"""A minimal Prometheus text-format 0.0.4 parser, enough to read our own exposition back."""

import re
from dataclasses import dataclass, field

_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


@dataclass
class Family:
    name: str
    help: str = ""
    type: str = ""
    samples: list[tuple[str, dict[str, str], float]] = field(default_factory=list)


def _unescape(value: str) -> str:
    out: list[str] = []
    it = iter(value)
    for ch in it:
        if ch == "\\":
            nxt = next(it, "")
            out.append({"n": "\n", "\\": "\\", '"': '"'}.get(nxt, "\\" + nxt))
        else:
            out.append(ch)
    return "".join(out)


def parse(text: str) -> dict[str, Family]:
    families: dict[str, Family] = {}
    current: Family | None = None
    assert text.endswith("\n")
    for line in text.splitlines():
        if line.startswith("# HELP "):
            name, _, help_ = line[7:].partition(" ")
            current = families.setdefault(name, Family(name))
            current.help = help_.replace("\\n", "\n").replace("\\\\", "\\")
        elif line.startswith("# TYPE "):
            name, _, kind = line[7:].partition(" ")
            current = families.setdefault(name, Family(name))
            current.type = kind
        elif line.startswith("#") or not line:
            raise AssertionError(f"unexpected line {line!r}")
        else:
            match = _SAMPLE.match(line)
            assert match, f"unparseable sample {line!r}"
            name, raw_labels, raw_value = match.groups()
            labels = {k: _unescape(v) for k, v in _LABEL.findall(raw_labels or "")}
            if raw_labels:  # the label block must be fully consumed by well-formed pairs
                rebuilt = ",".join(f'{k}="{v}"' for k, v in _LABEL.findall(raw_labels))
                assert rebuilt == raw_labels, f"malformed labels in {line!r}"
            assert current is not None and name.startswith(current.name)
            current.samples.append((name, labels, float(raw_value.replace("+Inf", "inf"))))
    return families


def sample_value(
    families: dict[str, Family], family: str, sample: str | None = None, **labels: str
) -> float | None:
    for name, got, value in families[family].samples:
        if name == (sample or family) and got == labels:
            return value
    return None
