import json

import pytest

from common import nsq
from payment_service.nsq.consumer import OrderConsumer
from payment_service.service import payment_service
from payment_service.service.payment_service import PaymentService


class FakeProducer:
    def __init__(self, error: Exception | None = None) -> None:
        self.published: list[tuple[str, bytes]] = []
        self.error = error

    async def publish(self, topic: str, body: bytes) -> None:
        if self.error is not None:
            raise self.error
        self.published.append((topic, body))


@pytest.fixture(autouse=True)
def instant_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payment_service, "MIN_DELAY_MILLIS", 0)
    monkeypatch.setattr(payment_service, "DELAY_SPREAD_MILLIS", 1)


def message(body: bytes) -> nsq.Message:
    return nsq.Message(id=b"0" * 16, timestamp=0, attempts=1, body=body)


async def test_publishes_the_outcome_like_the_go_service() -> None:
    producer = FakeProducer()
    consumer = OrderConsumer(PaymentService(), producer)

    body = b'{"order_id":"0b9f2c4e-a1b2-4c3d-8e9f-001122334455","user_id":"u","total_amount":"150000"}'
    await consumer._handle_order_created(message(body))

    [(topic, published)] = producer.published
    result = json.loads(published)
    assert topic == ("payment.success" if result["message"] == "payment processed successfully" else "payment.failed")
    # json.Marshal of a map[string]string: keys sorted, no spaces.
    assert published == (
        b'{"message":"' + result["message"].encode() + b'","order_id":"0b9f2c4e-a1b2-4c3d-8e9f-001122334455",'
        b'"payment_id":"pay_0b9f2c4e"}'
    )


@pytest.mark.parametrize("body", [b"not json", b'{"order_id":1}', b'{"order_id":"a"} trailing'])
async def test_an_unreadable_message_is_skipped(body: bytes) -> None:
    producer = FakeProducer()

    await OrderConsumer(PaymentService(), producer)._handle_order_created(message(body))

    assert producer.published == []


async def test_a_failed_publish_raises_so_the_message_is_requeued() -> None:
    consumer = OrderConsumer(PaymentService(), FakeProducer(nsq.NSQError("nsqd down")))

    with pytest.raises(nsq.NSQError):
        await consumer._handle_order_created(message(b'{"order_id":"o-1","total_amount":"10"}'))
