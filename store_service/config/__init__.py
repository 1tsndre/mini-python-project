import datetime as dt
import urllib.parse
from dataclasses import dataclass

from common.constant.env import ENV_DEVELOPMENT
from common.settings import Settings
from store_service.config import duration

INSECURE_JWT_SECRET = "your-super-secret-key-change-this"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AppConfig:
    port: str
    env: str
    read_timeout: dt.timedelta
    write_timeout: dt.timedelta
    idle_timeout: dt.timedelta
    shutdown_timeout: dt.timedelta
    request_timeout: dt.timedelta


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: str
    user: str
    password: str
    name: str
    ssl_mode: str

    def dsn(self) -> str:
        userinfo = urllib.parse.quote(self.user, safe="")
        if self.password:
            userinfo += ":" + urllib.parse.quote(self.password, safe="")
        query = urllib.parse.urlencode({"sslmode": self.ssl_mode})
        return f"postgres://{userinfo}@{self.host}:{self.port}/{self.name}?{query}"


@dataclass(frozen=True)
class RedisConfig:
    host: str
    port: str
    password: str
    db: int

    def addr(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class NSQConfig:
    lookupd_addr: str
    nsqd_addr: str


@dataclass(frozen=True)
class JWTConfig:
    secret: str
    access_expiry: dt.timedelta
    refresh_expiry: dt.timedelta


@dataclass(frozen=True)
class RateConfig:
    public: int
    auth: int
    login: int


@dataclass(frozen=True)
class UploadConfig:
    max_size: int
    dir: str


@dataclass(frozen=True)
class PaymentConfig:
    grpc_addr: str


@dataclass(frozen=True)
class Config:
    app: AppConfig
    db: DBConfig
    redis: RedisConfig
    nsq: NSQConfig
    jwt: JWTConfig
    rate: RateConfig
    upload: UploadConfig
    payment: PaymentConfig


class _Settings(Settings):
    def duration(self, key: str, default: str) -> dt.timedelta:
        value = self.string(key, default)
        try:
            return duration.parse(value)
        except ValueError as e:
            raise ConfigError(f"invalid {key}: {e}") from e


def load(env_file: str = ".env") -> Config:
    s = _Settings(env_file)

    access_expiry = s.duration("JWT_ACCESS_EXPIRY", "15m")
    refresh_expiry = s.duration("JWT_REFRESH_EXPIRY", "168h")
    read_timeout = s.duration("APP_READ_TIMEOUT", "15s")
    # Must exceed APP_REQUEST_TIMEOUT so a timed-out request can still be sent its 504.
    write_timeout = s.duration("APP_WRITE_TIMEOUT", "35s")
    idle_timeout = s.duration("APP_IDLE_TIMEOUT", "60s")
    shutdown_timeout = s.duration("APP_SHUTDOWN_TIMEOUT", "30s")
    request_timeout = s.duration("APP_REQUEST_TIMEOUT", "30s")

    # The server stops writing once the write timeout passes, so a request timeout at or beyond
    # it means the client gets a dropped connection instead of the 504.
    if write_timeout <= request_timeout:
        raise ConfigError(
            f"APP_WRITE_TIMEOUT ({duration.format(write_timeout)}) must be greater than "
            f"APP_REQUEST_TIMEOUT ({duration.format(request_timeout)})"
        )

    return Config(
        app=AppConfig(
            port=s.string("APP_PORT", "8080"),
            env=s.string("APP_ENV", ENV_DEVELOPMENT),
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            idle_timeout=idle_timeout,
            shutdown_timeout=shutdown_timeout,
            request_timeout=request_timeout,
        ),
        db=DBConfig(
            host=s.string("DB_HOST", "localhost"),
            port=s.string("DB_PORT", "5432"),
            user=s.string("DB_USER", "postgres"),
            password=s.string("DB_PASSWORD", ""),
            name=s.string("DB_NAME", "mini_python_ecommerce"),
            ssl_mode=s.string("DB_SSLMODE", "disable"),
        ),
        redis=RedisConfig(
            host=s.string("REDIS_HOST", "localhost"),
            port=s.string("REDIS_PORT", "6379"),
            password=s.string("REDIS_PASSWORD", ""),
            db=s.integer("REDIS_DB", 0),
        ),
        nsq=NSQConfig(
            lookupd_addr=s.string("NSQ_LOOKUPD_ADDR", "localhost:4161"),
            nsqd_addr=s.string("NSQD_ADDR", "localhost:4150"),
        ),
        jwt=JWTConfig(secret=s.string("JWT_SECRET", ""), access_expiry=access_expiry, refresh_expiry=refresh_expiry),
        rate=RateConfig(
            public=s.integer("RATE_LIMIT_PUBLIC", 60),
            auth=s.integer("RATE_LIMIT_AUTH", 120),
            login=s.integer("RATE_LIMIT_LOGIN", 10),
        ),
        upload=UploadConfig(max_size=s.integer("UPLOAD_MAX_SIZE", 5_242_880), dir=s.string("UPLOAD_DIR", "./uploads")),
        payment=PaymentConfig(grpc_addr=s.string("PAYMENT_GRPC_ADDR", "localhost:50051")),
    )
