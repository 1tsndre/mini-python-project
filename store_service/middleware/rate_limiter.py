import datetime as dt
import time
import uuid

from redis.asyncio import Redis
from starlette.requests import Request
from starlette.responses import Response

from common.response import Error, error_response
from store_service import constant
from store_service.middleware.auth import get_user_id
from store_service.middleware.helpers import Handler, Middleware, build_meta, client_addr


class RateLimiter:
    """A sliding-window rate limiter on a Redis sorted set of request timestamps."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    def limit(self, limit: int, window: dt.timedelta, key_type: constant.RateLimitKey) -> Middleware:
        def middleware(next_handler: Handler) -> Handler:
            async def handler(request: Request) -> Response:
                identifier, _ = client_addr(request.scope)
                user_id = get_user_id(request)
                if user_id:
                    identifier = user_id

                key = constant.KEY_RATE_LIMIT.format(key_type, identifier)
                try:
                    allowed, remaining, reset_at = await self._allow(key, limit, window)
                except Exception:  # noqa: BLE001 - if Redis fails, allow the request
                    return await next_handler(request)

                if allowed:
                    response = await next_handler(request)
                else:
                    response = error_response(
                        429, build_meta(), Error(constant.ErrorCode.RATE_LIMITED, message="rate limit exceeded")
                    )
                # The Go service sets these before calling the next handler, so when limiters are
                # chained the innermost one's values are sent. Here an inner limiter has already set
                # them by the time the response comes back, and they are kept.
                response.headers.setdefault(constant.HEADER_RATE_LIMIT_LIMIT, str(limit))
                response.headers.setdefault(constant.HEADER_RATE_LIMIT_REMAINING, str(remaining))
                response.headers.setdefault(constant.HEADER_RATE_LIMIT_RESET, str(reset_at))
                return response

            return handler

        return middleware

    async def _allow(self, key: str, limit: int, window: dt.timedelta) -> tuple[bool, int, int]:
        now = time.time()
        now_ms = int(now * 1000)
        window_start = now_ms - int(window.total_seconds() * 1000)
        reset_at = int(now + window.total_seconds())

        pipe = self._client.pipeline(transaction=False)
        pipe.zremrangebyscore(key, "0", str(window_start))
        pipe.zadd(key, {str(uuid.uuid4()): now_ms})
        pipe.zcard(key)
        pipe.expire(key, int(window.total_seconds()))
        results = await pipe.execute()

        count = int(results[2])
        remaining = max(limit - count, 0)
        return count <= limit, remaining, reset_at
