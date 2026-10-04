from collections.abc import Iterator

import pytest

from tests.mocks import Controller


@pytest.fixture
def ctrl() -> Iterator[Controller]:
    controller = Controller()
    yield controller
    controller.finish()
