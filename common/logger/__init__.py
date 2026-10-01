"""Structured logging with the request ID and user ID attached to every line of a request.

Like the Go service's zerolog setup: human-readable lines in development and one JSON object per
line in every other environment, with the time in RFC 3339 and the package, file and function
that logged.
"""

import datetime as dt
import logging
import os
import sys
from typing import Any, NoReturn

import structlog
from structlog.contextvars import bind_contextvars, get_contextvars, merge_contextvars
from structlog.typing import EventDict

from common.constant.env import ENV_DEVELOPMENT

REQUEST_ID_KEY = "request_id"
USER_ID_KEY = "user_id"

# zerolog's names for the levels structlog calls warning and critical.
_LEVEL_NAMES = {"warning": "warn", "critical": "fatal"}


def _add_time(_: Any, __: str, event_dict: EventDict) -> EventDict:
    event_dict["time"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return event_dict


def _rename_level(_: Any, __: str, event_dict: EventDict) -> EventDict:
    level = event_dict.get("level", "")
    event_dict["level"] = _LEVEL_NAMES.get(level, level)
    return event_dict


def _add_caller(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Names the caller like the Go logger: its package (directory), file and function."""
    pathname = event_dict.pop("pathname", "")
    event_dict["package"] = os.path.basename(os.path.dirname(pathname)) if pathname else "unknown"
    event_dict["file"] = event_dict.pop("filename", "unknown")
    event_dict["function"] = event_dict.pop("func_name", "unknown")
    return event_dict


def init(env: str) -> None:
    processors: list[Any] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        _rename_level,
        _add_time,
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.PATHNAME,
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            },
            # fatal logs on behalf of its caller.
            additional_ignores=[__name__],
        ),
        _add_caller,
    ]
    if env == ENV_DEVELOPMENT:
        processors += [structlog.dev.ConsoleRenderer(timestamp_key="time")]
    else:
        processors += [
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(separators=(",", ":")),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def fatal(message: str, error: BaseException | str, **fields: Any) -> NoReturn:
    """Like the Go logger's Fatal: logs at fatal level with the error, then exits with status 1."""
    structlog.get_logger().critical(message, error=str(error), **fields)
    sys.exit(1)


def with_request_id(request_id: str) -> None:
    bind_contextvars(**{REQUEST_ID_KEY: request_id})


def with_user_id(user_id: str) -> None:
    bind_contextvars(**{USER_ID_KEY: user_id})


def get_request_id() -> str:
    return get_contextvars().get(REQUEST_ID_KEY, "")


def get_user_id() -> str:
    return get_contextvars().get(USER_ID_KEY, "")
