import uuid

import structlog

from store_service import pagination
from store_service.model import CreateReviewRequest, Review, ReviewResponse
from store_service.repository.errors import DuplicateKeyError
from store_service.repository.review_repository import ReviewRepository
from store_service.service.errors import ConflictError, ForbiddenError, InternalError, ValidationError

log = structlog.get_logger()


class ReviewService:
    def __init__(self, repo: ReviewRepository) -> None:
        self._repo = repo

    async def create_review(
        self, user_id: uuid.UUID, product_id: uuid.UUID, req: CreateReviewRequest
    ) -> ReviewResponse:
        if req.rating < 1 or req.rating > 5:
            raise ValidationError("rating must be between 1 and 5")

        try:
            purchased = await self._repo.has_user_purchased(user_id, product_id)
        except Exception as e:
            raise InternalError("failed to verify purchase") from e
        if not purchased:
            raise ForbiddenError("you must purchase this product before reviewing")

        try:
            reviewed = await self._repo.has_user_reviewed(user_id, product_id)
        except Exception as e:
            raise InternalError("failed to check existing review") from e
        if reviewed:
            raise ConflictError("you have already reviewed this product")

        review = Review(user_id=user_id, product_id=product_id, rating=req.rating, comment=req.comment)

        try:
            await self._repo.create(review)
        except DuplicateKeyError as e:
            # A concurrent review by the same user passed the check above.
            raise ConflictError("you have already reviewed this product") from e
        except Exception as e:
            log.error("failed to create review", error=str(e))
            raise InternalError("failed to create review") from e

        return review.to_response()

    async def get_product_reviews(
        self, product_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[ReviewResponse], int]:
        page, per_page = pagination.normalize(page, per_page)

        try:
            reviews, total = await self._repo.find_by_product_id(product_id, page, per_page)
        except Exception as e:
            raise InternalError("failed to fetch reviews") from e

        return [r.to_response() for r in reviews], total
