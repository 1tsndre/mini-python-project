"""A small asyncio client for NSQ: a producer and a consumer that finds nsqd through nsqlookupd.

There is no maintained asyncio client for NSQ and the TCP protocol is small, so the services use
this one. It keeps go-nsq's defaults: one message in flight per connection, a message whose
handler fails is requeued after 90 seconds times its attempts (at most 15 minutes), a message is
given up after five attempts, and nsqlookupd is polled every minute.
"""

import asyncio
import json
import socket
import struct
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

log = structlog.get_logger()

MAGIC_V2 = b"  V2"
FRAME_TYPE_RESPONSE = 0
FRAME_TYPE_ERROR = 1
FRAME_TYPE_MESSAGE = 2
HEARTBEAT = b"_heartbeat_"
USER_AGENT = "mini-python-project/1.0"

DIAL_TIMEOUT = 1.0
READ_TIMEOUT = 60.0
LOOKUPD_POLL_INTERVAL = 60.0
LOOKUPD_POLL_TIMEOUT = 1.0
MAX_ATTEMPTS = 5
DEFAULT_REQUEUE_DELAY = 90.0
MAX_REQUEUE_DELAY = 15 * 60.0


class NSQError(Exception):
    """A command failed, or nsqd could not be reached."""


@dataclass
class Message:
    id: bytes
    timestamp: int
    attempts: int
    body: bytes


Handler = Callable[[Message], Awaitable[None]]


def _split_addr(addr: str) -> tuple[str, int]:
    host, _, port = addr.rpartition(":")
    return host, int(port)


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    size, frame_type = struct.unpack(">ii", await reader.readexactly(8))
    return frame_type, await reader.readexactly(size - 4)


def _command(name: str, *params: str, body: bytes | None = None) -> bytes:
    line = " ".join((name, *params)).encode() + b"\n"
    if body is None:
        return line
    return line + struct.pack(">I", len(body)) + body


def _identify_body() -> bytes:
    hostname = socket.gethostname()
    return json.dumps(
        {
            "client_id": hostname.split(".")[0],
            "hostname": hostname,
            "user_agent": USER_AGENT,
            "feature_negotiation": False,
        }
    ).encode()


async def _open(addr: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host, port = _split_addr(addr)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), DIAL_TIMEOUT)
    try:
        writer.write(MAGIC_V2 + _command("IDENTIFY", body=_identify_body()))
        await writer.drain()
        await _expect_ok(reader, writer)
    except BaseException:
        writer.close()
        raise
    return reader, writer


async def _expect_ok(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Waits for the response to the last command, answering heartbeats meanwhile."""
    while True:
        frame_type, data = await asyncio.wait_for(_read_frame(reader), READ_TIMEOUT)
        if frame_type == FRAME_TYPE_RESPONSE and data == HEARTBEAT:
            writer.write(_command("NOP"))
            continue
        if frame_type == FRAME_TYPE_RESPONSE:
            return
        if frame_type == FRAME_TYPE_ERROR:
            raise NSQError(data.decode(errors="replace"))
        raise NSQError(f"unexpected frame type {frame_type}")


class Producer:
    """Publishes to one nsqd, like go-nsq's Producer: the connection is opened on the first publish,
    kept open by answering nsqd's heartbeats, and replaced when it drops, so the next publish dials
    again instead of failing on a dead connection.
    """

    def __init__(self, addr: str) -> None:
        self._addr = addr
        self._lock = asyncio.Lock()
        self._conn: _ProducerConn | None = None

    async def publish(self, topic: str, body: bytes) -> None:
        """Raises NSQError if the message was not accepted."""
        async with self._lock:
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = await _ProducerConn.open(self._addr)
                await self._conn.request(_command("PUB", topic, body=body))
            except (OSError, EOFError, TimeoutError, NSQError) as e:
                await self._close()
                raise NSQError(f"failed to publish to {self._addr}: {e}") from e

    async def stop(self) -> None:
        async with self._lock:
            await self._close()

    async def _close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


class _ProducerConn:
    """A producer's connection. Its read loop answers heartbeats, hands each response to the request
    waiting for it, and notices when nsqd closes the connection.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._pending: asyncio.Future[None] | None = None
        self.closed = False
        self._task = asyncio.create_task(self._read_loop())

    @classmethod
    async def open(cls, addr: str) -> "_ProducerConn":
        return cls(*await _open(addr))

    async def request(self, command: bytes) -> None:
        """Sends a command and waits for its response; an error frame raises NSQError."""
        self._pending = asyncio.get_running_loop().create_future()
        self._writer.write(command)
        await self._writer.drain()
        await asyncio.wait_for(self._pending, READ_TIMEOUT)

    async def close(self) -> None:
        self.closed = True
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _read_loop(self) -> None:
        try:
            while True:
                frame_type, data = await _read_frame(self._reader)
                if frame_type == FRAME_TYPE_RESPONSE and data == HEARTBEAT:
                    self._writer.write(_command("NOP"))
                    continue
                pending, self._pending = self._pending, None
                if pending is None or pending.done():
                    continue
                if frame_type == FRAME_TYPE_RESPONSE:
                    pending.set_result(None)
                elif frame_type == FRAME_TYPE_ERROR:
                    pending.set_exception(NSQError(data.decode(errors="replace")))
                else:
                    pending.set_exception(NSQError(f"unexpected frame type {frame_type}"))
        except (OSError, EOFError):
            pass
        finally:
            self.closed = True
            if self._pending is not None and not self._pending.done():
                self._pending.set_exception(EOFError("connection closed by nsqd"))
            self._writer.close()


class Consumer:
    """Consumes a topic's channel from every nsqd that nsqlookupd lists for the topic.

    A handler that returns finishes the message; a handler that raises requeues it.
    """

    def __init__(self, topic: str, channel: str, handler: Handler, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._topic = topic
        self._channel = channel
        self._handler = handler
        self._max_attempts = max_attempts
        self._lookupd_url = ""
        self._connections: dict[str, asyncio.Task[None]] = {}
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._in_flight: set[asyncio.Task[None]] = set()
        self._poller: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def _label(self) -> str:
        return f"{self._topic}/{self._channel}"

    async def connect_to_lookupd(self, addr: str) -> None:
        """Queries nsqlookupd now, connects to the nsqd it lists, and keeps polling it.

        addr is host:port or a URL. Raises NSQError when it has no port, as go-nsq does.
        """
        self._lookupd_url = _lookup_url(addr, self._topic)
        await self._query_lookupd()
        self._poller = asyncio.create_task(self._poll_lookupd())

    async def stop(self) -> None:
        """Stops taking messages and waits for the ones in flight, so none is cut off mid-update."""
        self._stopping = True
        if self._poller is not None:
            self._poller.cancel()
        for writer in list(self._writers.values()):
            try:
                writer.write(_command("CLS"))
            except OSError:
                pass
        if self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)
        for task in list(self._connections.values()):
            task.cancel()
        await asyncio.gather(*self._connections.values(), return_exceptions=True)

    async def _poll_lookupd(self) -> None:
        while not self._stopping:
            await asyncio.sleep(LOOKUPD_POLL_INTERVAL)
            await self._query_lookupd()

    async def _query_lookupd(self) -> None:
        try:
            producers = await asyncio.to_thread(_lookup, self._lookupd_url)
        except Exception as e:  # noqa: BLE001 - a failed poll is retried on the next one, as in go-nsq
            log.warning("error querying nsqlookupd", topic=self._topic, url=self._lookupd_url, error=str(e))
            return
        for addr in producers:
            if addr not in self._connections and not self._stopping:
                self._connections[addr] = asyncio.create_task(self._consume(addr))

    async def _consume(self, addr: str) -> None:
        try:
            reader, writer = await _open(addr)
            self._writers[addr] = writer
            writer.write(_command("SUB", self._topic, self._channel))
            await writer.drain()
            await _expect_ok(reader, writer)
            writer.write(_command("RDY", "1"))
            await writer.drain()
            log.info("connected to nsqd", consumer=self._label, addr=addr)
            await self._read_loop(reader, writer)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the next lookupd poll reconnects
            if not self._stopping:
                log.warning("nsqd connection lost", consumer=self._label, addr=addr, error=str(e))
        finally:
            writer = self._writers.pop(addr, None)
            if writer is not None:
                writer.close()
            self._connections.pop(addr, None)

    async def _read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            frame_type, data = await _read_frame(reader)
            if frame_type == FRAME_TYPE_RESPONSE:
                if data == HEARTBEAT:
                    writer.write(_command("NOP"))
                continue
            if frame_type == FRAME_TYPE_ERROR:
                log.error("nsqd error", consumer=self._label, error=data.decode(errors="replace"))
                continue
            if frame_type == FRAME_TYPE_MESSAGE:
                timestamp, attempts = struct.unpack(">qH", data[:10])
                message = Message(id=data[10:26], timestamp=timestamp, attempts=attempts, body=data[26:])
                # Handled in its own task, so heartbeats are still answered while it runs.
                task = asyncio.create_task(self._handle(message, writer))
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)

    async def _handle(self, message: Message, writer: asyncio.StreamWriter) -> None:
        msg_id = message.id.decode()
        if message.attempts > self._max_attempts:
            log.warning("giving up on message", consumer=self._label, id=msg_id, attempts=message.attempts)
            self._respond(writer, _command("FIN", msg_id))
            return
        try:
            await self._handler(message)
        except Exception as e:  # noqa: BLE001 - any failure requeues the message
            log.error("handler returned error", consumer=self._label, id=msg_id, error=str(e))
            delay = min(DEFAULT_REQUEUE_DELAY * message.attempts, MAX_REQUEUE_DELAY)
            self._respond(writer, _command("REQ", msg_id, str(int(delay * 1000))))
            return
        self._respond(writer, _command("FIN", msg_id))

    def _respond(self, writer: asyncio.StreamWriter, command: bytes) -> None:
        try:
            writer.write(command)
            if not self._stopping:
                writer.write(_command("RDY", "1"))
        except OSError as e:
            log.warning("failed to respond to nsqd", consumer=self._label, error=str(e))


def _lookup_url(addr: str, topic: str) -> str:
    """The lookup endpoint for a topic, built from an nsqlookupd address as go-nsq builds it."""
    parts = urllib.parse.urlsplit(addr if "://" in addr else "http://" + addr)
    try:
        port = parts.port
    except ValueError as e:
        raise NSQError(str(e)) from e
    if port is None:
        raise NSQError("missing port")
    path = "/lookup" if parts.path in ("", "/") else parts.path
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True) + [("topic", topic)]
    query.sort(key=lambda item: item[0])
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, urllib.parse.urlencode(query), parts.fragment))


def _lookup(url: str) -> list[str]:
    """The nsqd TCP addresses nsqlookupd lists for a topic; none when the topic does not exist yet."""
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.nsq; version=1.0"})
    try:
        with urllib.request.urlopen(request, timeout=LOOKUPD_POLL_TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    data = payload.get("data", payload)
    return [f"{p['broadcast_address']}:{p['tcp_port']}" for p in data.get("producers") or []]
