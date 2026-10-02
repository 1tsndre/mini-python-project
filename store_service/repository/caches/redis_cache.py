import datetime as dt
from typing import Any

from redis.asyncio import Redis

from common import gojson


class RedisCache:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def get(self, key: str) -> bytes:
        value = await self._client.get(key)
        if value is None:
            raise KeyError(key)
        return value

    async def set(self, key: str, value: Any, ttl: dt.timedelta) -> None:
        data = gojson.marshal(value)
        if ttl:
            await self._client.set(key, data, px=int(ttl.total_seconds() * 1000))
        else:
            await self._client.set(key, data)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def exists(self, key: str) -> bool:
        return await self._client.exists(key) > 0
