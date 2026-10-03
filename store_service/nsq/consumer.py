import asyncio
from dataclasses import dataclass

import structlog

from common import gojson, nsq
from store_service import constant
from store_service.service.order_service import OrderService
from store_service.util import uuids

log = structlog.get_logger()

PROCESS_TIMEOUT_SECONDS = 30.0


@dataclass
class PaymentResultPayload:
    order_id: str = ""


class PaymentResultConsumer:
    def __init__(self, order_service: OrderService) -> None:
        self._order_service = order_service
        self._success_consumer: nsq.Consumer | None = None
        self._failed_consumer: nsq.Consumer | None = None

    async def start(self, lookupd_addr: str) -> None:
        self._success_consumer = nsq.Consumer(
            constant.TOPIC_PAYMENT_SUCCESS,
            constant.CHANNEL_STORE_SERVICE,
            lambda message: self._handle_payment_result(message, True),
        )
        await self._success_consumer.connect_to_lookupd(lookupd_addr)

        self._failed_consumer = nsq.Consumer(
            constant.TOPIC_PAYMENT_FAILED,
            constant.CHANNEL_STORE_SERVICE,
            lambda message: self._handle_payment_result(message, False),
        )
        await self._failed_consumer.connect_to_lookupd(lookupd_addr)

        log.info("NSQ payment result consumers started")

    async def stop(self) -> None:
        """Stops both consumers and waits for in-flight messages to finish, so no handler is still
        running when Redis and the database are closed afterwards.
        """
        consumers = [c for c in (self._success_consumer, self._failed_consumer) if c is not None]
        await asyncio.gather(*(c.stop() for c in consumers))
        log.info("NSQ payment result consumers stopped")

    async def _handle_payment_result(self, message: nsq.Message, success: bool) -> None:
        """Raises when the order could not be updated, so the message is requeued; a message that can
        never be processed is logged and finished.
        """
        try:
            payload = gojson.unmarshal(message.body, PaymentResultPayload)
        except gojson.DecodeError as e:
            log.error("failed to unmarshal payment result, skipping", error=str(e))
            return

        order_id = uuids.parse(payload.order_id)
        if order_id is None:
            log.error(
                "invalid order_id in payment result, skipping",
                error=f"invalid UUID: {payload.order_id!r}",
                order_id=payload.order_id,
            )
            return

        await asyncio.wait_for(self._order_service.process_payment_result(order_id, success), PROCESS_TIMEOUT_SECONDS)
