import datetime as dt
import uuid
from dataclasses import dataclass, field

from common import gojson
from store_service.model.zero import NIL_UUID, ZERO_TIME


@dataclass
class Review:
    id: uuid.UUID = NIL_UUID
    user_id: uuid.UUID = NIL_UUID
    product_id: uuid.UUID = NIL_UUID
    rating: int = 0
    comment: str = ""
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    # The reviewer's name, joined from users when reviews are listed.
    user_name: str = field(default="", metadata=gojson.SKIP)

    def to_response(self) -> "ReviewResponse":
        return ReviewResponse(
            id=self.id,
            user_id=self.user_id,
            user_name=self.user_name,
            product_id=self.product_id,
            rating=self.rating,
            comment=self.comment,
            created_at=self.created_at,
        )


@dataclass
class CreateReviewRequest:
    rating: int = 0
    comment: str = ""


@dataclass
class ReviewResponse:
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    product_id: uuid.UUID
    rating: int
    comment: str
    created_at: dt.datetime
