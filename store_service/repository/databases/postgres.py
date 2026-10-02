import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog

from common.constant.env import ENV_DEVELOPMENT
from store_service.config import duration
from store_service.repository.errors import translate_error

log = structlog.get_logger()


class _Executor:
    """Runs statements, translating driver errors and logging each one in development."""

    def __init__(self, target: asyncpg.Pool | asyncpg.Connection, log_queries: bool) -> None:
        self._target = target
        self._log_queries = log_queries

    async def fetch(self, query: str, *args: Any) -> Sequence[Any]:
        return await self._run(self._target.fetch, query, args, len)

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        return await self._run(self._target.fetchrow, query, args, lambda row: 0 if row is None else 1)

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self._run(self._target.fetchval, query, args, lambda _: 1)

    async def execute(self, query: str, *args: Any) -> int:
        status = await self._run(self._target.execute, query, args, _rows_affected)
        return _rows_affected(status)

    async def _run(self, call: Any, query: str, args: tuple[Any, ...], rows: Any) -> Any:
        start = time.perf_counter_ns()
        try:
            result = await call(query, *args)
        except Exception as e:
            if self._log_queries:
                log.error("query failed", error=str(e), **_fields(query, start, 0))
            raise translate_error(e) from e
        if self._log_queries:
            log.debug("query", **_fields(query, start, rows(result)))
        return result


class PostgresDB(_Executor):
    def __init__(self, pool: asyncpg.Pool, log_queries: bool) -> None:
        super().__init__(pool, log_queries)
        self._pool = pool

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Executor]:
        async with self._pool.acquire() as conn, conn.transaction():
            yield _Executor(conn, self._log_queries)

    async def close(self) -> None:
        await self._pool.close()


async def connect(dsn: str, env: str) -> PostgresDB:
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
    except Exception as e:
        raise ConnectionError(f"failed to connect to database: {e}") from e
    return PostgresDB(pool, log_queries=env == ENV_DEVELOPMENT)


def _rows_affected(status: str) -> int:
    """The row count of a command tag such as "UPDATE 3" or "INSERT 0 1"."""
    last = status.rsplit(" ", 1)[-1] if status else ""
    return int(last) if last.isdigit() else 0


def _fields(query: str, start: int, rows: int) -> dict[str, Any]:
    return {
        # The queries are written across several lines; log each one on a single line.
        "sql": " ".join(query.split()),
        "duration": duration.format_nanos(time.perf_counter_ns() - start),
        "rows": rows,
    }
