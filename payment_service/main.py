import asyncio
import signal

import grpc
import structlog

from common import logger, nsq
from payment_service.config import load
from payment_service.handler.payment_grpc import PaymentGRPCHandler
from payment_service.nsq.consumer import OrderConsumer
from payment_service.service.payment_service import PaymentService
from proto.payment import payment_pb2_grpc

log = structlog.get_logger()

# Go's GracefulStop waits for the calls in flight; a payment takes at most two seconds, so this
# bound only matters for a hung call.
GRACEFUL_STOP_SECONDS = 30.0


async def main() -> None:
    cfg = load()
    logger.init(cfg.app.env)

    payment_svc = PaymentService()

    nsq_producer = nsq.Producer(cfg.nsq.nsqd_addr)

    order_consumer = OrderConsumer(payment_svc, nsq_producer)
    try:
        await order_consumer.start(cfg.nsq.lookupd_addr)
    except nsq.NSQError as e:
        logger.fatal("failed to start NSQ consumer", e)

    grpc_server = grpc.aio.server()
    payment_pb2_grpc.add_PaymentServiceServicer_to_server(PaymentGRPCHandler(payment_svc), grpc_server)
    try:
        grpc_server.add_insecure_port("[::]:" + cfg.payment.grpc_port)
    except RuntimeError as e:
        logger.fatal("failed to listen on gRPC port", e)

    log.info("payment gRPC server starting", port=cfg.payment.grpc_port)
    await grpc_server.start()

    quit_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, quit_event.set)
    await quit_event.wait()

    log.info("shutting down payment service...")
    await order_consumer.stop()
    await nsq_producer.stop()
    await grpc_server.stop(GRACEFUL_STOP_SECONDS)
    log.info("payment service stopped")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
