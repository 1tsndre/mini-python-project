import asyncio
import contextlib
import datetime as dt

import structlog

from store_service.config import duration
from store_service.service.order_service import OrderService

log = structlog.get_logger()


class PaymentRetrier:
    """Periodically republishes order.created for orders that are still pending, so an order whose
    original publish failed (or whose message was lost) still reaches the payment service.
    """

    def __init__(
        self, order_service: OrderService, interval: dt.timedelta, older_than: dt.timedelta, batch_size: int
    ) -> None:
        self._order_service = order_service
        self._interval = interval
        self._older_than = older_than
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        log.info(
            "payment retry worker started",
            interval=duration.format(self._interval),
            older_than=duration.format(self._older_than),
        )

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval.total_seconds())
            await self.run_once()

    async def run_once(self) -> None:
        try:
            count = await asyncio.wait_for(
                self._order_service.retry_pending_payments(self._older_than, self._batch_size),
                self._interval.total_seconds(),
            )
        except Exception as e:  # noqa: BLE001
            log.error("failed to retry pending payments", error=str(e))
            return
        if count > 0:
            log.info("republished pending orders", count=count)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        log.info("payment retry worker stopped")
