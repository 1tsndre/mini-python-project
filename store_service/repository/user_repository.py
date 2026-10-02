import uuid

from store_service import constant
from store_service.model import User
from store_service.repository.databases import Database
from store_service.repository.errors import RecordNotFoundError
from store_service.repository.rows import scan, scan_into

USER_COLUMNS = "id, email, password, name, role, created_at, updated_at"


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, user: User) -> None:
        row = await self._db.fetchrow(
            """
		INSERT INTO users (email, password, name, role)
		VALUES ($1, $2, $3, $4)
		RETURNING """
            + USER_COLUMNS,
            user.email,
            user.password,
            user.name,
            user.role,
        )
        scan_into(user, row)

    async def find_by_id(self, user_id: uuid.UUID) -> User:
        row = await self._db.fetchrow("SELECT " + USER_COLUMNS + " FROM users WHERE id = $1", user_id)
        if row is None:
            raise RecordNotFoundError()
        return scan(User, row)

    async def find_by_email(self, email: str) -> User:
        """Matches case-insensitively, which also finds accounts stored with mixed-case emails before
        registration started lower-casing them. If such an old account has a case variant, the
        older one wins.
        """
        row = await self._db.fetchrow(
            """
		SELECT """
            + USER_COLUMNS
            + """
		FROM users
		WHERE lower(email) = lower($1)
		ORDER BY created_at, id
		LIMIT 1""",
            email,
        )
        if row is None:
            raise RecordNotFoundError()
        return scan(User, row)

    async def update_role(self, user_id: uuid.UUID, role: constant.Role) -> None:
        await self._db.execute("UPDATE users SET role = $1, updated_at = NOW() WHERE id = $2", role, user_id)
