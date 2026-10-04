import asyncpg

from store_service.repository.errors import DuplicateKeyError, ForeignKeyViolationError, RecordNotFoundError


def db_error() -> Exception:
    return ConnectionError("db error")


def not_found() -> Exception:
    return RecordNotFoundError()


def duplicate_key() -> Exception:
    return DuplicateKeyError(asyncpg.UniqueViolationError("duplicate key"))


def foreign_key_violation() -> Exception:
    return ForeignKeyViolationError(asyncpg.ForeignKeyViolationError("foreign key violation"))
