from collections.abc import AsyncIterator

import grpc
import pytest

from payment_service.handler.payment_grpc import PaymentGRPCHandler
from payment_service.service import payment_service
from payment_service.service.payment_service import PaymentService
from proto.payment import payment_pb2, payment_pb2_grpc


@pytest.fixture
async def stub(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[payment_pb2_grpc.PaymentServiceStub]:
    monkeypatch.setattr(payment_service, "MIN_DELAY_MILLIS", 0)
    monkeypatch.setattr(payment_service, "DELAY_SPREAD_MILLIS", 1)
    server = grpc.aio.server()
    payment_pb2_grpc.add_PaymentServiceServicer_to_server(PaymentGRPCHandler(PaymentService()), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield payment_pb2_grpc.PaymentServiceStub(channel)
    await server.stop(None)


async def test_unknown_order_is_not_found(stub: payment_pb2_grpc.PaymentServiceStub) -> None:
    resp = await stub.GetPaymentStatus(payment_pb2.GetPaymentStatusRequest(order_id="missing"))

    assert (resp.order_id, resp.status, resp.payment_id) == ("missing", "not_found", "")


async def test_processed_payment_is_reported(stub: payment_pb2_grpc.PaymentServiceStub) -> None:
    req = payment_pb2.ProcessPaymentRequest(order_id="0b9f2c4e-a1b2", amount="150000", method="mock")
    processed = await stub.ProcessPayment(req)
    status = await stub.GetPaymentStatus(payment_pb2.GetPaymentStatusRequest(order_id="0b9f2c4e-a1b2"))

    assert processed.payment_id == "pay_0b9f2c4e"
    assert processed.status == ("success" if processed.success else "failed")
    assert (status.payment_id, status.status, status.amount, status.method) == (
        "pay_0b9f2c4e",
        processed.status,
        "150000",
        "mock",
    )
