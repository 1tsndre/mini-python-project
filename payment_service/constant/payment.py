from enum import StrEnum


class PaymentStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    # Reported for an order the service has no payment for.
    NOT_FOUND = "not_found"


class PaymentMethod(StrEnum):
    MOCK = "mock"


PAYMENT_MESSAGE_SUCCESS = "payment processed successfully"
PAYMENT_MESSAGE_DECLINED = "payment declined"
