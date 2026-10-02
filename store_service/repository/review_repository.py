import uuid

from store_service import constant, pagination
from store_service.model import Review
from store_service.repository.databases import Database
from store_service.repository.rows import scan, scan_into

REVIEW_COLUMNS = "id, user_id, product_id, rating, comment, created_at, updated_at"


class ReviewRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, review: Review) -> None:
        row = await self._db.fetchrow(
            """
		INSERT INTO reviews (user_id, product_id, rating, comment)
		VALUES ($1, $2, $3, $4)
		RETURNING """
            + REVIEW_COLUMNS,
            review.user_id,
            review.product_id,
            review.rating,
            review.comment,
        )
        scan_into(review, row)

    async def find_by_product_id(self, product_id: uuid.UUID, page: int, per_page: int) -> tuple[list[Review], int]:
        total = await self._db.fetchval("SELECT COUNT(*) FROM reviews WHERE product_id = $1", product_id)

        rows = await self._db.fetch(
            """
		SELECT r.id, r.user_id, r.product_id, r.rating, r.comment, r.created_at, r.updated_at,
			u.name AS user_name
		FROM reviews r
		JOIN users u ON u.id = r.user_id
		WHERE r.product_id = $1
		ORDER BY r.created_at DESC, r.id DESC
		LIMIT $2 OFFSET $3""",
            product_id,
            per_page,
            pagination.offset(page, per_page),
        )
        return [scan(Review, row) for row in rows], total

    async def has_user_reviewed(self, user_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        return await self._db.fetchval(
            "SELECT EXISTS (SELECT 1 FROM reviews WHERE user_id = $1 AND product_id = $2)", user_id, product_id
        )

    async def has_user_purchased(self, user_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        return await self._db.fetchval(
            """
		SELECT EXISTS (
			SELECT 1
			FROM order_items oi
			JOIN orders o ON o.id = oi.order_id
			WHERE o.user_id = $1 AND oi.product_id = $2 AND o.status IN ($3, $4)
		)""",
            user_id,
            product_id,
            constant.OrderStatus.SHIPPED,
            constant.OrderStatus.COMPLETED,
        )
