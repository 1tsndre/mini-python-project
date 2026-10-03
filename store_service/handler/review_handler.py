from starlette.requests import Request
from starlette.responses import Response

from common.response import Pagination, success, success_with_pagination
from store_service import pagination
from store_service.handler.helpers import decode_json, path_uuid, query, reject, user_id
from store_service.middleware.helpers import build_meta
from store_service.model import CreateReviewRequest
from store_service.service.review_service import ReviewService
from store_service.util import conv

INVALID_PRODUCT_ID = "invalid product id"


class ReviewHandler:
    def __init__(self, service: ReviewService) -> None:
        self._service = service

    async def create_review(self, request: Request) -> Response:
        uid = user_id(request)
        product_id = path_uuid(request, "id", INVALID_PRODUCT_ID)
        req = await decode_json(request, CreateReviewRequest)

        if req.rating < 1 or req.rating > 5:
            reject("rating", "must be between 1 and 5")

        return success(201, await self._service.create_review(uid, product_id, req), build_meta())

    async def get_product_reviews(self, request: Request) -> Response:
        product_id = path_uuid(request, "id", INVALID_PRODUCT_ID)

        page = conv.atoi(query(request, "page"))
        per_page = conv.atoi(query(request, "per_page"))

        reviews, total = await self._service.get_product_reviews(product_id, page, per_page)

        page, per_page = pagination.normalize(page, per_page)
        return success_with_pagination(
            200,
            reviews,
            build_meta(),
            Pagination(
                current_page=page,
                per_page=per_page,
                total_items=total,
                total_pages=pagination.total_pages(total, per_page),
            ),
        )
