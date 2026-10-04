import uuid
from unittest.mock import ANY

import pytest

from store_service.model import CreateReviewRequest, Review
from store_service.repository.review_repository import ReviewRepository
from store_service.service.errors import ConflictError, ForbiddenError, InternalError, ValidationError
from store_service.service.review_service import ReviewService
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, duplicate_key

USER_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


def new_service(ctrl: Controller) -> tuple[ReviewService, ReviewRepository]:
    repo = ctrl.mock(ReviewRepository)
    return ReviewService(repo), repo


class TestCreateReview:
    async def test_success(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.has_user_purchased, USER_ID, PRODUCT_ID, returns=True)
        ctrl.expect(repo.has_user_reviewed, USER_ID, PRODUCT_ID, returns=False)
        ctrl.expect(repo.create, ANY)

        resp = await svc.create_review(USER_ID, PRODUCT_ID, CreateReviewRequest(rating=5, comment="Great product"))

        assert resp.rating == 5

    @pytest.mark.parametrize("rating", [0, 6], ids=["too low", "too high"])
    async def test_rating_out_of_range(self, ctrl: Controller, rating: int) -> None:
        svc, _ = new_service(ctrl)

        with pytest.raises(ValidationError, match="rating must be between 1 and 5"):
            await svc.create_review(USER_ID, PRODUCT_ID, CreateReviewRequest(rating=rating, comment="x"))

    async def test_user_has_not_purchased_product(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.has_user_purchased, USER_ID, PRODUCT_ID, returns=False)

        with pytest.raises(ForbiddenError, match="you must purchase this product"):
            await svc.create_review(USER_ID, PRODUCT_ID, CreateReviewRequest(rating=4, comment="Good"))

    async def test_user_already_reviewed(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.has_user_purchased, USER_ID, PRODUCT_ID, returns=True)
        ctrl.expect(repo.has_user_reviewed, USER_ID, PRODUCT_ID, returns=True)

        with pytest.raises(ConflictError, match="you have already reviewed"):
            await svc.create_review(USER_ID, PRODUCT_ID, CreateReviewRequest(rating=3, comment="Okay"))

    async def test_create_fails(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.has_user_purchased, USER_ID, PRODUCT_ID, returns=True)
        ctrl.expect(repo.has_user_reviewed, USER_ID, PRODUCT_ID, returns=False)
        ctrl.expect(repo.create, ANY, raises=db_error())

        with pytest.raises(InternalError, match="failed to create review"):
            await svc.create_review(USER_ID, PRODUCT_ID, CreateReviewRequest(rating=5, comment="Great"))

    async def test_concurrent_duplicate_review(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.has_user_purchased, USER_ID, PRODUCT_ID, returns=True)
        ctrl.expect(repo.has_user_reviewed, USER_ID, PRODUCT_ID, returns=False)
        ctrl.expect(repo.create, ANY, raises=duplicate_key())

        with pytest.raises(ConflictError, match="you have already reviewed"):
            await svc.create_review(USER_ID, PRODUCT_ID, CreateReviewRequest(rating=5, comment="Great"))


class TestGetProductReviews:
    async def test_success_includes_the_reviewers_name(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        reviews = [
            Review(id=uuid.uuid4(), product_id=PRODUCT_ID, rating=5, user_name="Andi"),
            Review(id=uuid.uuid4(), product_id=PRODUCT_ID, rating=3, user_name="Budi"),
        ]
        ctrl.expect(repo.find_by_product_id, PRODUCT_ID, 1, 10, returns=(reviews, 2))

        resp, total = await svc.get_product_reviews(PRODUCT_ID, 1, 10)

        assert total == 2
        assert [r.user_name for r in resp] == ["Andi", "Budi"]

    async def test_no_reviews_is_an_empty_list(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_product_id, PRODUCT_ID, 1, 10, returns=([], 0))

        resp, total = await svc.get_product_reviews(PRODUCT_ID, 1, 10)

        assert resp == []
        assert total == 0

    async def test_repository_fails(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_product_id, PRODUCT_ID, 1, 10, raises=db_error())

        with pytest.raises(InternalError, match="failed to fetch reviews"):
            await svc.get_product_reviews(PRODUCT_ID, 1, 10)
