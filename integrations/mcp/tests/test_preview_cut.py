"""A secret straddling the preview cut must not leak a prefix (redaction runs after truncation)."""

from abb_conformance import SECRET

from blackbox_mcp.wrap import _preview as preview


def test_secret_straddling_the_cut_leaves_no_prefix() -> None:
    cut = 0
    for pad in range(4040, 4100):
        out = preview({"note": "x" * pad + " " + SECRET})
        if isinstance(out, dict):  # fits whole: the SDK redacts the intact secret
            continue
        cut += 1
        whole = SECRET in out  # intact secrets are redacted by the SDK; fragments would not be
        assert whole or ("sk-ant" not in out and "Zq9" not in out), pad
    assert cut > 10


def test_short_values_stay_structured_and_long_unbroken_text_is_summarised() -> None:
    assert preview({"a": 1}) == {"a": 1}
    assert preview("y" * 10_000) == "<10002 characters>"
