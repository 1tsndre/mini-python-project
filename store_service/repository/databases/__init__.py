from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class Executor(Protocol):
    """Runs statements on the pool, or on one connection inside a transaction."""

    async def fetch(self, query: str, *args: Any) -> Sequence[Any]: ...

    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...

    async def fetchval(self, query: str, *args: Any) -> Any: ...

    async def execute(self, query: str, *args: Any) -> int:
        """Runs a statement and returns the number of rows it changed."""
        ...


class Database(Executor, Protocol):
    def transaction(self) -> AbstractAsyncContextManager[Executor]:
        """Runs the block in a transaction, committed when the block finishes and rolled back when
        it raises.
        """
        ...

    async def close(self) -> None: ...
