import uuid
from unittest.mock import ANY

import pytest

from store_service.model import Category, CreateCategoryRequest
from store_service.repository.category_repository import CategoryRepository
from store_service.service.category_service import CategoryService
from store_service.service.errors import ConflictError, InternalError, NotFoundError
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, duplicate_key, foreign_key_violation, not_found

CATEGORY_ID = uuid.uuid4()


def new_service(ctrl: Controller) -> tuple[CategoryService, CategoryRepository]:
    repo = ctrl.mock(CategoryRepository)
    return CategoryService(repo), repo


class TestCreateCategory:
    req = CreateCategoryRequest(name="Electronics")

    async def test_success(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.create, ANY)

        assert (await svc.create_category(self.req)).name == "Electronics"

    async def test_duplicate_name(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.create, ANY, raises=duplicate_key())

        with pytest.raises(ConflictError, match="category already exists"):
            await svc.create_category(self.req)

    async def test_create_fails(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.create, ANY, raises=ConnectionResetError("connection reset"))

        with pytest.raises(InternalError, match="failed to create category"):
            await svc.create_category(self.req)


class TestGetAllCategories:
    @pytest.mark.parametrize(
        "categories",
        [[Category(id=uuid.uuid4(), name="Electronics"), Category(id=uuid.uuid4(), name="Clothing")], []],
        ids=["with categories", "empty"],
    )
    async def test_success(self, ctrl: Controller, categories: list[Category]) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_all, returns=categories)

        assert len(await svc.get_all_categories()) == len(categories)

    async def test_db_error(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_all, raises=db_error())

        with pytest.raises(InternalError):
            await svc.get_all_categories()


class TestDeleteCategory:
    async def test_success(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_id, CATEGORY_ID, returns=Category(id=CATEGORY_ID, name="Electronics"))
        ctrl.expect(repo.delete, CATEGORY_ID)

        await svc.delete_category(CATEGORY_ID)

    async def test_not_found(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_id, CATEGORY_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="category not found"):
            await svc.delete_category(CATEGORY_ID)

    async def test_still_referenced_by_products(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_id, CATEGORY_ID, returns=Category(id=CATEGORY_ID, name="Electronics"))
        ctrl.expect(repo.delete, CATEGORY_ID, raises=foreign_key_violation())

        with pytest.raises(ConflictError, match="in use"):
            await svc.delete_category(CATEGORY_ID)
