import asyncio
import contextlib
import signal
import socket
import sys
import time
from collections.abc import Iterator

import structlog
import uvicorn
from redis.asyncio import Redis
from uvicorn.protocols.http import h11_impl

from common import logger, nsq
from common.jwt import JWTManager
from common.upload import Uploader
from store_service import constant, nethttp
from store_service.config import INSECURE_JWT_SECRET, ConfigError, load
from store_service.grpcclient.payment import PaymentClient
from store_service.handler.auth_handler import AuthHandler
from store_service.handler.cart_handler import CartHandler
from store_service.handler.category_handler import CategoryHandler
from store_service.handler.order_handler import OrderHandler
from store_service.handler.product_handler import ProductHandler
from store_service.handler.review_handler import ReviewHandler
from store_service.handler.store_handler import StoreHandler
from store_service.nsq.consumer import PaymentResultConsumer
from store_service.repository.caches.redis_cache import RedisCache
from store_service.repository.cart_repository import CartRepository
from store_service.repository.category_repository import CategoryRepository
from store_service.repository.databases import postgres
from store_service.repository.order_repository import OrderRepository
from store_service.repository.product_repository import ProductRepository
from store_service.repository.review_repository import ReviewRepository
from store_service.repository.store_repository import StoreRepository
from store_service.repository.user_repository import UserRepository
from store_service.router import Handlers, create_app
from store_service.service.auth_service import AuthService
from store_service.service.cart_lock import CartLock
from store_service.service.cart_service import CartService
from store_service.service.category_service import CategoryService
from store_service.service.order_service import OrderService
from store_service.service.product_service import ProductService
from store_service.service.review_service import ReviewService
from store_service.service.store_service import StoreService
from store_service.worker.payment_retry import PaymentRetrier

log = structlog.get_logger()

# Uploads are the largest bodies accepted; leave 1 MiB of headroom for the multipart encoding.
MULTIPART_HEADROOM_BYTES = 1 << 20

# Status lines read like Go's where Python's reason phrases differ (413, 416).
h11_impl.STATUS_PHRASES.update({code: text.encode() for code, text in nethttp.GO_STATUS_TEXT.items()})


def _fatal(message: str) -> None:
    """Like Go's log.Fatal: a timestamped line on stderr, then exit status 1."""
    print(time.strftime("%Y/%m/%d %H:%M:%S"), message, file=sys.stderr)
    sys.exit(1)


def _listen(port: int) -> socket.socket:
    """Listens on every interface like Go's net.Listen(":port"): IPv6 and IPv4 on one dual-stack
    socket where the system supports it, IPv4 alone otherwise.
    """
    if socket.has_dualstack_ipv6():
        return socket.create_server(("::", port), family=socket.AF_INET6, dualstack_ipv6=True)
    return socket.create_server(("0.0.0.0", port))


class _Server(uvicorn.Server):
    """Stops on SIGINT or SIGTERM like the Go service. uvicorn's own handling re-raises the signal once
    the server has drained, which would end the process before main() closes the connections; these
    handlers stay installed instead, so later signals are ignored as with Go's signal.Notify.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)
        yield

    def _shutdown(self) -> None:
        if not self.should_exit:
            log.info("shutting down server...")
            self.should_exit = True


async def main() -> None:
    try:
        cfg = load()
    except ConfigError as e:
        _fatal(f"failed to load config: {e}")
        return

    if not cfg.jwt.secret or cfg.jwt.secret == INSECURE_JWT_SECRET:
        _fatal("JWT_SECRET must be set to a strong, non-default value")

    logger.init(cfg.app.env)

    try:
        db = await postgres.connect(cfg.db.dsn(), cfg.app.env)
    except ConnectionError as e:
        logger.fatal("failed to connect to database", e)
    log.info("connected to database")

    redis_client = Redis(
        host=cfg.redis.host, port=int(cfg.redis.port), password=cfg.redis.password or None, db=cfg.redis.db
    )
    try:
        await redis_client.ping()
    except Exception as e:  # noqa: BLE001
        logger.fatal("failed to connect to redis", e)
    log.info("connected to redis")

    cart_lock = CartLock(redis_client)

    nsq_producer = nsq.Producer(cfg.nsq.nsqd_addr)
    log.info("connected to NSQ")

    cache = RedisCache(redis_client)

    user_repo = UserRepository(db)
    store_repo = StoreRepository(db)
    category_repo = CategoryRepository(db)
    product_repo = ProductRepository(db, cache)
    cart_repo = CartRepository(db, cache)
    order_repo = OrderRepository(db, cache)
    review_repo = ReviewRepository(db)

    jwt_manager = JWTManager(cfg.jwt.secret, cfg.jwt.access_expiry, cfg.jwt.refresh_expiry)

    auth_service = AuthService(user_repo, jwt_manager)
    store_service = StoreService(store_repo, user_repo)
    category_service = CategoryService(category_repo)
    product_service = ProductService(product_repo, store_repo)
    cart_service = CartService(cart_repo, product_repo, cart_lock)
    order_service = OrderService(order_repo, cart_repo, store_repo, cart_lock, nsq_producer)
    review_service = ReviewService(review_repo)

    uploader = Uploader(cfg.upload.dir, cfg.upload.max_size)

    payment_client = PaymentClient(cfg.payment.grpc_addr, cfg.app.request_timeout)
    log.info("payment gRPC client ready", addr=cfg.payment.grpc_addr)

    handlers = Handlers(
        auth=AuthHandler(auth_service),
        store=StoreHandler(store_service, uploader),
        category=CategoryHandler(category_service),
        product=ProductHandler(product_service, uploader),
        cart=CartHandler(cart_service),
        order=OrderHandler(order_service, payment_client),
        review=ReviewHandler(review_service),
    )

    payment_consumer = PaymentResultConsumer(order_service)
    try:
        await payment_consumer.start(cfg.nsq.lookupd_addr)
    except Exception as e:  # noqa: BLE001
        log.warning("failed to start NSQ consumer, payment callbacks won't work", error=str(e))

    payment_retrier = PaymentRetrier(
        order_service,
        constant.PAYMENT_RETRY_INTERVAL,
        constant.PAYMENT_RETRY_AFTER,
        constant.PAYMENT_RETRY_BATCH_SIZE,
    )
    payment_retrier.start()

    max_body_bytes = cfg.upload.max_size + MULTIPART_HEADROOM_BYTES
    app = create_app(
        handlers, jwt_manager, redis_client, cfg.upload.dir, max_body_bytes, cfg.app.request_timeout, cfg.rate
    )

    server = _Server(
        uvicorn.Config(
            app,
            http="h11",
            log_config=None,
            access_log=False,
            server_header=False,
            timeout_keep_alive=int(cfg.app.idle_timeout.total_seconds()),
            timeout_graceful_shutdown=int(cfg.app.shutdown_timeout.total_seconds()),
        )
    )
    log.info("server starting", port=cfg.app.port)
    try:
        sock = _listen(int(cfg.app.port))
    except (OSError, OverflowError, ValueError) as e:
        logger.fatal("server failed", e)
    # Drains in-flight requests on SIGINT/SIGTERM before returning: they still need the database,
    # Redis, NSQ and gRPC, which are closed only afterwards.
    await server.serve(sockets=[sock])

    await payment_retrier.stop()
    await payment_consumer.stop()
    await nsq_producer.stop()
    await payment_client.close()
    await redis_client.aclose()
    await db.close()
    log.info("server stopped")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
