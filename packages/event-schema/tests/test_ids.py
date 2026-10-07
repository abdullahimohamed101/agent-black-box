import uuid
from collections.abc import Callable

import pytest

from abb_event_schema.ids import (
    IdGenerator,
    IdKind,
    from_uuid,
    id_pattern,
    new_id,
    parse_id,
    to_uuid,
)


def fixed_entropy(value: int) -> Callable[[int], bytes]:
    return lambda n: value.to_bytes(n, "big")


def test_generated_id_has_prefix_and_26_char_ulid() -> None:
    value = new_id(IdKind.EVENT)
    assert value.startswith("evt_") and len(value) == 4 + 26
    assert parse_id(value, IdKind.EVENT).kind is IdKind.EVENT


def test_timestamp_round_trips_and_ids_sort_by_time() -> None:
    clock = iter([1_000, 2_000, 3_000])
    gen = IdGenerator(clock_ms=lambda: next(clock), entropy=fixed_entropy(0))
    ids = [gen.new_id(IdKind.RUN) for _ in range(3)]
    assert ids == sorted(ids)
    assert [parse_id(i).timestamp_ms for i in ids] == [1_000, 2_000, 3_000]


def test_monotonic_within_the_same_millisecond() -> None:
    gen = IdGenerator(clock_ms=lambda: 5_000, entropy=fixed_entropy(41))
    ids = [gen.new_id(IdKind.SPAN) for _ in range(5)]
    assert ids == sorted(ids) and len(set(ids)) == 5


def test_clock_going_backwards_does_not_break_order() -> None:
    clock = iter([9_000, 8_000])
    gen = IdGenerator(clock_ms=lambda: next(clock), entropy=fixed_entropy(1))
    first, second = gen.new_id(IdKind.EVENT), gen.new_id(IdKind.EVENT)
    assert first < second


def test_randomness_overflow_carries_into_the_timestamp() -> None:
    gen = IdGenerator(clock_ms=lambda: 7_000, entropy=fixed_entropy((1 << 80) - 1))
    first, second = gen.new_ulid(), gen.new_ulid()
    assert first < second


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "evt_",
        "evt_01ARZ3NDEKTSV4RRFFQ69G5FA",  # 25 chars
        "evt_01ARZ3NDEKTSV4RRFFQ69G5FAVV",  # 27 chars
        "evt_01arz3ndektsv4rrffq69g5fav",  # lowercase: only canonical form is accepted
        "evt_01ARZ3NDEKTSV4RRFFQ69G5FAI",  # I is not in the alphabet
        "evt_81ARZ3NDEKTSV4RRFFQ69G5FAV",  # timestamp overflow (first char > 7)
        "EVT_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "xyz_01ARZ3NDEKTSV4RRFFQ69G5FAV",  # unknown prefix
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",  # no prefix
        "evt_01ARZ3NDEKTSV4RRFFQ69G5FAV\n",  # trailing newline
    ],
)
def test_malformed_ids_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_id(bad)


def test_wrong_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_id(new_id(IdKind.RUN), IdKind.EVENT)


def test_uuid_round_trip_is_lossless() -> None:
    original = new_id(IdKind.WORKSPACE)
    as_uuid = to_uuid(original)
    assert isinstance(as_uuid, uuid.UUID)
    assert from_uuid(IdKind.WORKSPACE, as_uuid) == original


def test_pattern_matches_generated_ids_and_only_the_right_kind() -> None:
    import re

    assert re.match(id_pattern(IdKind.TRACE), new_id(IdKind.TRACE))
    assert not re.match(id_pattern(IdKind.TRACE), new_id(IdKind.SPAN))


def test_many_ids_are_unique() -> None:
    assert len({new_id(IdKind.EVENT) for _ in range(20_000)}) == 20_000
