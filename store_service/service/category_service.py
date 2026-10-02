import uuid

import structlog

from store_service.model import Category, CategoryResponse, CreateCategoryRequest, UpdateCategoryRequest
from store_service.repository.category_repository import CategoryRepository
from store_service.repository.errors import DuplicateKeyError, ForeignKeyViolationError
from store_service.service.errors import ConflictError, InternalError, NotFoundError

log = structlog.get_logger()


class CategoryService:
    def __init__(self, repo: CategoryRepository) -> None:
        self._repo = repo

    async def create_category(self, req: CreateCategoryRequest) -> CategoryResponse:
        category = Category(name=req.name)

        try:
            await self._repo.create(category)
        except Exception as e:
            log.error("failed to create category", error=str(e))
            if isinstance(e, DuplicateKeyError):
                raise ConflictError("category already exists") from e
            raise InternalError("failed to create category") from e

        return category.to_response()

    async def get_all_categories(self) -> list[CategoryResponse]:
        try:
            categories = await self._repo.find_all()
        except Exception as e:
            log.error("failed to fetch categories", error=str(e))
            raise InternalError("failed to fetch categories") from e
        return [c.to_response() for c in categories]

    async def update_category(self, category_id: uuid.UUID, req: UpdateCategoryRequest) -> CategoryResponse:
        category = await self._find_category(category_id)

        if req.name:
            category.name = req.name

        try:
            await self._repo.update(category)
        except Exception as e:
            log.error("failed to update category", error=str(e))
            if isinstance(e, DuplicateKeyError):
                raise ConflictError("category already exists") from e
            raise InternalError("failed to update category") from e

        return category.to_response()

    async def delete_category(self, category_id: uuid.UUID) -> None:
        await self._find_category(category_id)

        try:
            await self._repo.delete(category_id)
        except ForeignKeyViolationError as e:
            raise ConflictError("category is in use by existing products") from e
        except Exception as e:
            log.error("failed to delete category", error=str(e))
            raise InternalError("failed to delete category") from e

    async def _find_category(self, category_id: uuid.UUID) -> Category:
        try:
            return await self._repo.find_by_id(category_id)
        except Exception as e:
            raise NotFoundError("category not found") from e
