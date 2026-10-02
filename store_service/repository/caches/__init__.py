import datetime as dt
from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> bytes:
        """The cached value; raises KeyError when the key is missing."""
        ...

    async def set(self, key: str, value: Any, ttl: dt.timedelta) -> None:
        """Stores the value as JSON; a zero ttl means no expiry."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...
