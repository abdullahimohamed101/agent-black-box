from collections.abc import Iterator

import pytest

from tests.helpers import StubServer, start_stub, stop_stub


@pytest.fixture
def stub() -> Iterator[StubServer]:
    server = start_stub()
    yield server
    stop_stub(server)
