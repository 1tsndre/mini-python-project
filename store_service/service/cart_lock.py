"""The per-user cart lock shared by cart updates and checkout, so they never interleave on the same
cart. A Redis lock with the defaults of the Go service's redsync mutex: an 8 second expiry and up
to 32 attempts, 50-250 ms apart.
"""

import asyncio
import base64
import os
import random
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from store_service import constant
from store_service.service.errors import InternalError

EXPIRY_SECONDS = 8.0
TRIES = 32
MIN_RETRY_DELAY = 0.050
MAX_RETRY_DELAY = 0.250
# The share of the expiry redsync treats as clock drift.
DRIFT_FACTOR = 0.01

# Deletes the lock only if it still holds our value, so an expired lock taken over by someone
# else survives.
_UNLOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end"""


class CartLock:
    def __init__(self, client: Redis) -> None:
        self._client = client

    @asynccontextmanager
    async def hold(self, user_id: uuid.UUID) -> AsyncIterator[None]:
        """Holds the user's cart lock for the block; raises InternalError if it cannot be taken."""
        key = constant.KEY_CART_LOCK.format(user_id)
        value = base64.b64encode(os.urandom(16)).decode()
        await self._acquire(key, value)
        try:
            yield
        finally:
            await self._release(key, value)

    async def _acquire(self, key: str, value: str) -> None:
        for attempt in range(TRIES):
            if attempt:
                await asyncio.sleep(random.uniform(MIN_RETRY_DELAY, MAX_RETRY_DELAY))
            start = time.monotonic()
            try:
                acquired = await self._client.set(key, value, nx=True, px=int(EXPIRY_SECONDS * 1000))
            except Exception:  # noqa: BLE001 - an unreachable Redis is just a failed attempt
                acquired = False
            elapsed = time.monotonic() - start
            if acquired and elapsed + EXPIRY_SECONDS * DRIFT_FACTOR + 0.002 < EXPIRY_SECONDS:
                return
            if acquired:
                await self._release(key, value)
        raise InternalError("failed to acquire cart lock, please try again")

    async def _release(self, key: str, value: str) -> None:
        try:
            await self._client.eval(_UNLOCK_SCRIPT, 1, key, value)
        except Exception:  # noqa: BLE001, S110 - the lock expires on its own
            pass
