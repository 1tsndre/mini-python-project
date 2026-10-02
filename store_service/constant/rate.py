from enum import StrEnum


class RateLimitKey(StrEnum):
    PUBLIC = "public"
    AUTH = "auth"
    LOGIN = "login"
