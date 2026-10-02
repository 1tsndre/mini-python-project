import uuid

from store_service.model import Category
from store_service.repository.databases import Database
from store_service.repository.errors import RecordNotFoundError
from store_service.repository.rows import scan, scan_into

CATEGORY_COLUMNS = "id, name, created_at, updated_at"


class CategoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, category: Category) -> None:
        row = await self._db.fetchrow(
            "INSERT INTO categories (name) VALUES ($1) RETURNING " + CATEGORY_COLUMNS, category.name
        )
        scan_into(category, row)

    async def find_all(self) -> list[Category]:
        rows = await self._db.fetch("SELECT " + CATEGORY_COLUMNS + " FROM categories ORDER BY name ASC")
        return [scan(Category, row) for row in rows]

    async def find_by_id(self, category_id: uuid.UUID) -> Category:
        row = await self._db.fetchrow("SELECT " + CATEGORY_COLUMNS + " FROM categories WHERE id = $1", category_id)
        if row is None:
            raise RecordNotFoundError()
        return scan(Category, row)

    async def update(self, category: Category) -> None:
        row = await self._db.fetchrow(
            "UPDATE categories SET name = $1, updated_at = NOW() WHERE id = $2 RETURNING " + CATEGORY_COLUMNS,
            category.name,
            category.id,
        )
        if row is None:
            raise RecordNotFoundError()
        scan_into(category, row)

    async def delete(self, category_id: uuid.UUID) -> None:
        await self._db.execute("DELETE FROM categories WHERE id = $1", category_id)
