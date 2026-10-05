import datetime as dt
import pathlib

import pytest

from store_service.config import ConfigError, load

KEYS = [
    "APP_PORT", "APP_ENV", "APP_READ_TIMEOUT", "APP_WRITE_TIMEOUT", "APP_IDLE_TIMEOUT", "APP_SHUTDOWN_TIMEOUT",
    "APP_REQUEST_TIMEOUT", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_SSLMODE", "REDIS_HOST",
    "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "NSQ_LOOKUPD_ADDR", "NSQD_ADDR", "JWT_SECRET", "JWT_ACCESS_EXPIRY",
    "JWT_REFRESH_EXPIRY", "RATE_LIMIT_PUBLIC", "RATE_LIMIT_AUTH", "RATE_LIMIT_LOGIN", "UPLOAD_MAX_SIZE", "UPLOAD_DIR",
    "PAYMENT_GRPC_ADDR",
]  # fmt: skip


@pytest.fixture
def env_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A missing .env and none of the service's variables set, like the Go test's package directory."""
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path / ".env"


def test_defaults_match_the_go_service(env_file: pathlib.Path) -> None:
    cfg = load(str(env_file))

    assert cfg.app.port == "8080"
    assert cfg.app.env == "development"
    assert cfg.db.name == "mini_python_ecommerce"
    assert cfg.db.dsn() == "postgres://postgres@localhost:5432/mini_python_ecommerce?sslmode=disable"
    assert cfg.redis.addr() == "localhost:6379"
    assert cfg.jwt.secret == ""
    assert cfg.jwt.access_expiry == dt.timedelta(minutes=15)
    assert cfg.jwt.refresh_expiry == dt.timedelta(hours=168)
    assert (cfg.rate.public, cfg.rate.auth, cfg.rate.login) == (60, 120, 10)
    assert cfg.upload.max_size == 5_242_880
    assert cfg.payment.grpc_addr == "localhost:50051"


def test_default_timeouts_are_consistent(env_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty values fall back to the defaults (viper ignores empty env vars).
    monkeypatch.setenv("APP_WRITE_TIMEOUT", "")
    monkeypatch.setenv("APP_REQUEST_TIMEOUT", "")

    cfg = load(str(env_file))

    assert cfg.app.request_timeout == dt.timedelta(seconds=30)
    assert cfg.app.write_timeout > cfg.app.request_timeout


@pytest.mark.parametrize(
    ("write", "request_timeout", "want_error"),
    [("15s", "30s", True), ("30s", "30s", True), ("35s", "30s", False)],
    ids=["write shorter than request", "write equal to request", "write longer than request"],
)
def test_rejects_write_timeout_not_above_request_timeout(
    env_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch, write: str, request_timeout: str, want_error: bool
) -> None:
    monkeypatch.setenv("APP_WRITE_TIMEOUT", write)
    monkeypatch.setenv("APP_REQUEST_TIMEOUT", request_timeout)

    if not want_error:
        load(str(env_file))
        return
    with pytest.raises(ConfigError) as error:
        load(str(env_file))
    assert str(error.value) == (
        f"APP_WRITE_TIMEOUT ({write}) must be greater than APP_REQUEST_TIMEOUT ({request_timeout})"
    )


def test_invalid_duration_names_the_variable(env_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ACCESS_EXPIRY", "15 minutes")

    with pytest.raises(ConfigError) as error:
        load(str(env_file))
    assert str(error.value) == 'invalid JWT_ACCESS_EXPIRY: time: unknown unit " minutes" in duration "15 minutes"'


def test_env_file_is_the_fallback(env_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file.write_text("DB_NAME=from_file\napp_port=9090\nREDIS_DB=\nRATE_LIMIT_LOGIN=many\nDB_HOST=file-host\n")
    monkeypatch.setenv("DB_HOST", "env-host")
    monkeypatch.setenv("DB_NAME", "")

    cfg = load(str(env_file))

    assert cfg.db.host == "env-host", "a set environment variable wins"
    assert cfg.db.name == "from_file", "an empty environment variable counts as unset"
    assert cfg.app.port == "9090", "keys in .env match case-insensitively"
    assert cfg.redis.db == 0, "an empty value in .env is a value"
    assert cfg.rate.login == 0, "a number that does not parse reads as 0, like viper"
