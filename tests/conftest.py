from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest

from patterns.db import load, temp_database


@pytest.fixture
def make_db() -> Iterator[Callable[..., str]]:
    """``make_db("01_constraints.sql")`` returns the URL of a fresh database with those files loaded.

    Every database is dropped when the test ends, so tests never see each other's data.
    """
    stack = ExitStack()

    def make(*files: str) -> str:
        url = stack.enter_context(temp_database())
        load(url, *files)
        return url

    yield make
    stack.close()
