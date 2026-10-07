from abb_event_schema import SCHEMA_VERSION


def test_schema_version_is_1_0() -> None:
    assert SCHEMA_VERSION == "1.0"
