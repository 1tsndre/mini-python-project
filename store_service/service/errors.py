"""Business failures the client is told about. Each kind maps to one HTTP status and error code,
so a message that contains user input (a product name, a requested status) can never change the
response. The messages are part of the API and must not leak internal details.
"""


class ServiceError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """The requested resource does not exist (404)."""


class ForbiddenError(ServiceError):
    """The user may not act on the resource (403)."""


class ConflictError(ServiceError):
    """The request conflicts with existing data, e.g. a duplicate (409)."""


class ValidationError(ServiceError):
    """The request is invalid (400, VALIDATION_ERROR)."""


class UnauthorizedError(ServiceError):
    """The credentials or token are not valid (401)."""


class InternalError(ServiceError):
    """An expected failure such as an unavailable database, reported with a generic message (500)."""


class InsufficientStockError(ServiceError):
    """A product cannot cover the requested quantity (400, INSUFFICIENT_STOCK)."""


class InvalidStatusError(ServiceError):
    """An order cannot make the requested status change (400, INVALID_STATUS)."""
