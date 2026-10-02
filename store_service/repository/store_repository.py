import uuid

from store_service.model import Store
from store_service.repository.databases import Database
from store_service.repository.errors import RecordNotFoundError
from store_service.repository.rows import scan, scan_into

STORE_COLUMNS = "id, user_id, name, description, logo_url, created_at, updated_at"


class StoreRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, store: Store) -> None:
        row = await self._db.fetchrow(
            """
		INSERT INTO stores (user_id, name, description, logo_url)
		VALUES ($1, $2, $3, $4)
		RETURNING """
            + STORE_COLUMNS,
            store.user_id,
            store.name,
            store.description,
            store.logo_url,
        )
        scan_into(store, row)

    async def find_by_id(self, store_id: uuid.UUID) -> Store:
        return await self._find("SELECT " + STORE_COLUMNS + " FROM stores WHERE id = $1", store_id)

    async def find_by_user_id(self, user_id: uuid.UUID) -> Store:
        return await self._find("SELECT " + STORE_COLUMNS + " FROM stores WHERE user_id = $1", user_id)

    async def update(self, store: Store) -> None:
        row = await self._db.fetchrow(
            """
		UPDATE stores
		SET name = $1, description = $2, logo_url = $3, updated_at = NOW()
		WHERE id = $4
		RETURNING """
            + STORE_COLUMNS,
            store.name,
            store.description,
            store.logo_url,
            store.id,
        )
        if row is None:
            raise RecordNotFoundError()
        scan_into(store, row)

    async def delete(self, store_id: uuid.UUID) -> None:
        await self._db.execute("DELETE FROM stores WHERE id = $1", store_id)

    async def _find(self, query: str, value: uuid.UUID) -> Store:
        row = await self._db.fetchrow(query, value)
        if row is None:
            raise RecordNotFoundError()
        return scan(Store, row)
