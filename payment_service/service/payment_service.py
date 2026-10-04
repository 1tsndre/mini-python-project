import asyncio
import random
from dataclasses import dataclass

import structlog

from payment_service import constant

log = structlog.get_logger()

MIN_DELAY_MILLIS = 500
DELAY_SPREAD_MILLIS = 1500
SUCCESS_RATE = 0.9
PAYMENT_ID_PREFIX = "pay_"
PAYMENT_ID_ORDER_BYTES = 8


@dataclass
class PaymentResult:
    order_id: str
    success: bool
    payment_id: str
    message: str


@dataclass
class PaymentRecord:
    order_id: str
    payment_id: str
    status: constant.PaymentStatus
    amount: str
    method: str

    def to_result(self) -> PaymentResult:
        success = self.status == constant.PaymentStatus.SUCCESS
        return PaymentResult(
            order_id=self.order_id,
            success=success,
            payment_id=self.payment_id,
            message=constant.PAYMENT_MESSAGE_SUCCESS if success else constant.PAYMENT_MESSAGE_DECLINED,
        )


class PaymentService:
    def __init__(self) -> None:
        self._records: dict[str, PaymentRecord] = {}

    async def process_payment(self, order_id: str, amount: str, method: str) -> PaymentResult:
        """Idempotent per order: a redelivered or republished request for an order that was already
        processed returns the recorded outcome instead of charging again with a fresh random result.
        """
        rec = self.get_status(order_id)
        if rec is not None:
            log.info("payment already processed, returning recorded result", order_id=order_id)
            return rec.to_result()

        await asyncio.sleep((MIN_DELAY_MILLIS + random.randrange(DELAY_SPREAD_MILLIS)) / 1000)

        success = random.random() < SUCCESS_RATE

        # The first eight bytes of the order ID, as Go slices the string.
        suffix = order_id.encode()[:PAYMENT_ID_ORDER_BYTES].decode(errors="replace")
        payment_id = PAYMENT_ID_PREFIX + suffix

        if success:
            status = constant.PaymentStatus.SUCCESS
            log.info(constant.PAYMENT_MESSAGE_SUCCESS, order_id=order_id, amount=amount)
        else:
            status = constant.PaymentStatus.FAILED
            log.warning(constant.PAYMENT_MESSAGE_DECLINED, order_id=order_id, amount=amount)

        # A concurrent duplicate may have finished first; keep whichever was recorded first.
        record = PaymentRecord(order_id=order_id, payment_id=payment_id, status=status, amount=amount, method=method)
        return self._records.setdefault(order_id, record).to_result()

    def get_status(self, order_id: str) -> PaymentRecord | None:
        return self._records.get(order_id)
