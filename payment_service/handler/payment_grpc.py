import asyncio

import grpc

from payment_service import constant
from payment_service.service.payment_service import PaymentService
from proto.payment import payment_pb2, payment_pb2_grpc


class PaymentGRPCHandler(payment_pb2_grpc.PaymentServiceServicer):
    def __init__(self, service: PaymentService) -> None:
        self._service = service

    async def ProcessPayment(  # noqa: N802 - the gRPC method name
        self, request: payment_pb2.ProcessPaymentRequest, context: grpc.aio.ServicerContext
    ) -> payment_pb2.ProcessPaymentResponse:
        # Shielded so a caller that gives up does not cut the payment off halfway: as in the Go
        # service, it is still recorded and reported by GetPaymentStatus.
        result = await asyncio.shield(self._service.process_payment(request.order_id, request.amount, request.method))
        return payment_pb2.ProcessPaymentResponse(
            success=result.success,
            payment_id=result.payment_id,
            status=constant.PaymentStatus.SUCCESS if result.success else constant.PaymentStatus.FAILED,
            message=result.message,
        )

    async def GetPaymentStatus(  # noqa: N802 - the gRPC method name
        self, request: payment_pb2.GetPaymentStatusRequest, context: grpc.aio.ServicerContext
    ) -> payment_pb2.GetPaymentStatusResponse:
        rec = self._service.get_status(request.order_id)
        if rec is None:
            return payment_pb2.GetPaymentStatusResponse(
                order_id=request.order_id, status=constant.PaymentStatus.NOT_FOUND
            )
        return payment_pb2.GetPaymentStatusResponse(
            payment_id=rec.payment_id,
            order_id=rec.order_id,
            status=rec.status,
            amount=rec.amount,
            method=rec.method,
        )
