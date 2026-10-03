import datetime as dt

import grpc

from proto.payment import payment_pb2, payment_pb2_grpc
from store_service.model import PaymentStatusResponse


class PaymentClient:
    """The payment service's gRPC API; the connection is opened on the first call."""

    def __init__(self, addr: str, timeout: dt.timedelta) -> None:
        self._channel = grpc.aio.insecure_channel(addr)
        self._client = payment_pb2_grpc.PaymentServiceStub(self._channel)
        # A call gets as long as the request it serves, like the Go client's request context.
        self._timeout = timeout.total_seconds()

    async def get_status(self, order_id: str) -> PaymentStatusResponse:
        resp = await self._client.GetPaymentStatus(
            payment_pb2.GetPaymentStatusRequest(order_id=order_id), timeout=self._timeout
        )
        return PaymentStatusResponse(
            order_id=resp.order_id,
            payment_id=resp.payment_id,
            status=resp.status,
            amount=resp.amount,
            method=resp.method,
        )

    async def close(self) -> None:
        await self._channel.close()
