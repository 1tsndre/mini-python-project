from dataclasses import dataclass

import structlog

from common import gojson, nsq
from payment_service import constant
from payment_service.service.payment_service import PaymentService

log = structlog.get_logger()


@dataclass
class OrderCreatedPayload:
    order_id: str = ""
    user_id: str = ""
    total_amount: str = ""


class OrderConsumer:
    """Pays for every order.created message and publishes the outcome to payment.success or
    payment.failed. A result that cannot be published is retried through a requeue.
    """

    def __init__(self, payment_service: PaymentService, producer: nsq.Producer) -> None:
        self._payment_service = payment_service
        self._producer = producer
        self._consumer: nsq.Consumer | None = None

    async def start(self, lookupd_addr: str) -> None:
        consumer = nsq.Consumer(
            constant.TOPIC_ORDER_CREATED, constant.CHANNEL_PAYMENT_SERVICE, self._handle_order_created
        )
        await consumer.connect_to_lookupd(lookupd_addr)
        self._consumer = consumer
        log.info("NSQ order consumer started")

    async def stop(self) -> None:
        """Stops consuming and waits for in-flight messages to finish, so their results can still be
        published before the producer is stopped.
        """
        if self._consumer is None:
            return
        await self._consumer.stop()
        log.info("NSQ order consumer stopped")

    async def _handle_order_created(self, message: nsq.Message) -> None:
        try:
            payload = gojson.unmarshal(message.body, OrderCreatedPayload)
        except gojson.DecodeError as e:
            log.error("failed to unmarshal order.created, skipping", error=str(e))
            return

        log.info("processing payment", order_id=payload.order_id, amount=payload.total_amount)

        result = await self._payment_service.process_payment(
            payload.order_id, payload.total_amount, constant.PaymentMethod.MOCK
        )

        response = gojson.marshal(
            {"order_id": result.order_id, "payment_id": result.payment_id, "message": result.message}
        )
        topic = constant.TOPIC_PAYMENT_SUCCESS if result.success else constant.TOPIC_PAYMENT_FAILED
        await self._producer.publish(topic, response)
