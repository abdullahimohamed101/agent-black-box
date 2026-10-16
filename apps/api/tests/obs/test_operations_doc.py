"""The metrics table in docs/OPERATIONS.md lists exactly the catalogue (no drift in either way)."""

import re
from pathlib import Path

from abb_api.obs.catalogue import build_observability

OPERATIONS = Path(__file__).resolve().parents[4] / "docs" / "OPERATIONS.md"
ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \| (counter|gauge|histogram) \| ([^|]*) \|", re.M)


def test_every_catalogue_metric_is_documented_with_its_type_and_labels() -> None:
    documented = {
        name: (kind, labels.strip()) for name, kind, labels in ROW.findall(OPERATIONS.read_text())
    }
    declared = build_observability().registry.metrics
    assert set(documented) == set(declared), (
        f"missing from the doc: {sorted(set(declared) - set(documented))}; "
        f"not in the catalogue: {sorted(set(documented) - set(declared))}"
    )
    for name, metric in declared.items():
        kind, labels = documented[name]
        assert kind == metric.type, name
        assert labels == (", ".join(metric.label_names) or "-"), name
