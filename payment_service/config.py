from dataclasses import dataclass

from common.constant.env import ENV_PRODUCTION
from common.settings import Settings


@dataclass(frozen=True)
class AppConfig:
    env: str


@dataclass(frozen=True)
class NSQConfig:
    lookupd_addr: str
    nsqd_addr: str


@dataclass(frozen=True)
class PaymentConfig:
    grpc_port: str


@dataclass(frozen=True)
class Config:
    app: AppConfig
    nsq: NSQConfig
    payment: PaymentConfig


def load(env_file: str = ".env") -> Config:
    s = Settings(env_file)
    return Config(
        app=AppConfig(env=s.string("APP_ENV", ENV_PRODUCTION)),
        nsq=NSQConfig(
            lookupd_addr=s.string("NSQ_LOOKUPD_ADDR", "localhost:4161"),
            nsqd_addr=s.string("NSQD_ADDR", "localhost:4150"),
        ),
        payment=PaymentConfig(grpc_port=s.string("PAYMENT_GRPC_PORT", "50051")),
    )
