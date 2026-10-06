import asyncio

import pytest

from payment_service.service import payment_service
from payment_service.service.payment_service import PaymentService


@pytest.fixture(autouse=True)
def instant_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    # Payments still yield to the event loop, so concurrent duplicates still overlap.
    monkeypatch.setattr(payment_service, "MIN_DELAY_MILLIS", 0)
    monkeypatch.setattr(payment_service, "DELAY_SPREAD_MILLIS", 1)


async def test_process_payment_is_idempotent() -> None:
    svc = PaymentService()

    first = await svc.process_payment("order-1", "100.00", "mock")
    second = await svc.process_payment("order-1", "100.00", "mock")

    assert (first.success, first.payment_id, first.message) == (second.success, second.payment_id, second.message)
    rec = svc.get_status("order-1")
    assert rec is not None
    assert rec.payment_id == first.payment_id


async def test_concurrent_duplicates_agree() -> None:
    svc = PaymentService()

    results = await asyncio.gather(*(svc.process_payment("order-2", "50.00", "mock") for _ in range(5)))

    rec = svc.get_status("order-2")
    assert rec is not None
    for result in results:
        assert result.success == (rec.status == "success"), "every duplicate must report the recorded outcome"


def test_get_status_unknown() -> None:
    assert PaymentService().get_status("missing") is None


async def test_payment_id_is_pay_and_the_first_eight_bytes_of_the_order_id() -> None:
    svc = PaymentService()

    assert (await svc.process_payment("0b9f2c4e-a1b2-4c3d", "1", "mock")).payment_id == "pay_0b9f2c4e"
    assert (await svc.process_payment("short", "1", "mock")).payment_id == "pay_short"
