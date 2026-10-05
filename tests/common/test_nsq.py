import asyncio
import struct
from collections.abc import Callable

import pytest

from common import nsq

OK, ERROR, MESSAGE = 0, 1, 2


class FakeNSQD:
    """Speaks just enough of the nsqd TCP protocol for the client: it records every command and
    hands out the queued messages, one per RDY.
    """

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.published: list[tuple[str, bytes]] = []
        self.messages: list[bytes] = []
        self.pub_error: bytes | None = None
        self.drop_after_pub = False
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()

    def heartbeat(self) -> None:
        """Sends a heartbeat to every connected client, as nsqd does every 30 seconds."""
        for writer in self._writers:
            self._send(writer, OK, b"_heartbeat_")

    def queue(self, msg_id: bytes, body: bytes, attempts: int = 1) -> None:
        self.messages.append(struct.pack(">qH", 0, attempts) + msg_id.ljust(16, b"0") + body)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        try:
            assert await reader.readexactly(4) == b"  V2"
            while line := (await reader.readline()).rstrip(b"\n").decode():
                self.commands.append(line)
                name, *params = line.split(" ")
                body = None
                if name in ("IDENTIFY", "PUB"):
                    (size,) = struct.unpack(">I", await reader.readexactly(4))
                    body = await reader.readexactly(size)
                if name == "PUB":
                    if self.pub_error is not None:
                        self._send(writer, ERROR, self.pub_error)
                    else:
                        self.published.append((params[0], body or b""))
                        self._send(writer, OK, b"OK")
                    if self.drop_after_pub:
                        break
                elif name in ("IDENTIFY", "SUB"):
                    self._send(writer, OK, b"OK")
                elif name == "RDY" and params[0] != "0" and self.messages:
                    self._send(writer, MESSAGE, self.messages.pop(0))
                elif name == "CLS":
                    self._send(writer, OK, b"CLOSE_WAIT")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    @staticmethod
    def _send(writer: asyncio.StreamWriter, frame_type: int, data: bytes) -> None:
        writer.write(struct.pack(">ii", len(data) + 4, frame_type) + data)


async def until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture
async def nsqd() -> FakeNSQD:
    server = FakeNSQD()
    yield server
    await server.stop()


@pytest.mark.parametrize(
    ("addr", "url"),
    [
        ("localhost:4161", "http://localhost:4161/lookup?topic=order.created"),
        ("http://nsqlookupd:4161", "http://nsqlookupd:4161/lookup?topic=order.created"),
        ("http://h:4161/", "http://h:4161/lookup?topic=order.created"),
        ("https://h:1/x?b=2&a=1", "https://h:1/x?a=1&b=2&topic=order.created"),
    ],
)
def test_lookup_url_is_built_like_go_nsq(addr: str, url: str) -> None:
    assert nsq._lookup_url(addr, "order.created") == url


@pytest.mark.parametrize("addr", ["localhost", "http://localhost", "localhost:abc"])
def test_lookup_address_without_a_valid_port_is_rejected(addr: str) -> None:
    with pytest.raises(nsq.NSQError):
        nsq._lookup_url(addr, "order.created")


async def test_producer_identifies_and_publishes(nsqd: FakeNSQD) -> None:
    producer = nsq.Producer(await nsqd.start())

    await producer.publish("order.created", b'{"order_id":"1"}')
    await producer.publish("order.created", b'{"order_id":"2"}')
    await producer.stop()

    assert nsqd.commands[0] == "IDENTIFY"
    assert nsqd.published == [("order.created", b'{"order_id":"1"}'), ("order.created", b'{"order_id":"2"}')]


async def test_producer_reports_an_error_frame(nsqd: FakeNSQD) -> None:
    nsqd.pub_error = b"E_BAD_TOPIC PUB topic name is not valid"
    producer = nsq.Producer(await nsqd.start())

    with pytest.raises(nsq.NSQError, match="E_BAD_TOPIC"):
        await producer.publish("bad topic", b"x")
    await producer.stop()


async def test_producer_redials_when_nsqd_closes_the_connection(nsqd: FakeNSQD) -> None:
    nsqd.drop_after_pub = True
    producer = nsq.Producer(await nsqd.start())
    await producer.publish("t", b"1")
    await until(lambda: nsqd.commands.count("PUB t") == 1)
    await asyncio.sleep(0.05)  # the read loop notices the close

    # Like go-nsq, the next publish dials again instead of failing on the dead connection.
    await producer.publish("t", b"2")
    await producer.stop()

    assert nsqd.published == [("t", b"1"), ("t", b"2")]
    assert nsqd.commands.count("IDENTIFY") == 2


async def test_producer_answers_heartbeats_while_idle(nsqd: FakeNSQD) -> None:
    producer = nsq.Producer(await nsqd.start())
    await producer.publish("t", b"1")

    nsqd.heartbeat()
    await until(lambda: "NOP" in nsqd.commands)
    await producer.publish("t", b"2")
    await producer.stop()

    assert nsqd.published == [("t", b"1"), ("t", b"2")]
    assert nsqd.commands.count("IDENTIFY") == 1, "the connection stays open"


async def test_producer_without_nsqd_fails_fast() -> None:
    with pytest.raises(nsq.NSQError):
        await nsq.Producer("127.0.0.1:1").publish("t", b"x")


async def test_consumer_finishes_handled_messages_and_requeues_failures(
    nsqd: FakeNSQD, monkeypatch: pytest.MonkeyPatch
) -> None:
    addr = await nsqd.start()
    monkeypatch.setattr(nsq, "_lookup", lambda url: [addr])
    nsqd.queue(b"ok", b"good")
    nsqd.queue(b"bad", b"fail", attempts=2)
    nsqd.queue(b"old", b"never handled", attempts=nsq.MAX_ATTEMPTS + 1)
    handled: list[bytes] = []

    async def handler(message: nsq.Message) -> None:
        handled.append(message.body)
        if message.body == b"fail":
            raise RuntimeError("database down")

    consumer = nsq.Consumer("payment.success", "store-service", handler)
    await consumer.connect_to_lookupd("lookupd:4161")
    await until(lambda: len([c for c in nsqd.commands if c.startswith(("FIN", "REQ"))]) == 3)
    await consumer.stop()

    assert "SUB payment.success store-service" in nsqd.commands
    assert handled == [b"good", b"fail"], "a message past its attempts is given up without handling"
    assert f"FIN {'ok'.ljust(16, '0')}" in nsqd.commands
    # Requeued like go-nsq: 90 seconds per attempt so far.
    assert f"REQ {'bad'.ljust(16, '0')} 180000" in nsqd.commands
    assert f"FIN {'old'.ljust(16, '0')}" in nsqd.commands
    assert "CLS" in nsqd.commands


async def test_consumer_stop_waits_for_the_message_in_flight(nsqd: FakeNSQD, monkeypatch: pytest.MonkeyPatch) -> None:
    addr = await nsqd.start()
    monkeypatch.setattr(nsq, "_lookup", lambda url: [addr])
    nsqd.queue(b"slow", b"slow")
    started, finished = asyncio.Event(), asyncio.Event()

    async def handler(message: nsq.Message) -> None:
        started.set()
        await asyncio.sleep(0.2)
        finished.set()

    consumer = nsq.Consumer("order.created", "payment-service", handler)
    await consumer.connect_to_lookupd("lookupd:4161")
    await asyncio.wait_for(started.wait(), 2)
    await consumer.stop()

    assert finished.is_set()


async def test_consumer_survives_an_unreachable_lookupd(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(url: str) -> list[str]:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(nsq, "_lookup", unreachable)

    async def handler(message: nsq.Message) -> None:
        raise AssertionError("no message expected")

    consumer = nsq.Consumer("order.created", "payment-service", handler)
    await consumer.connect_to_lookupd("127.0.0.1:1")
    await consumer.stop()
