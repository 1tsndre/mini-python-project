import uuid
from unittest.mock import ANY

import pytest

from store_service import constant
from store_service.model import CreateStoreRequest, Store, UpdateStoreRequest
from store_service.repository.store_repository import StoreRepository
from store_service.repository.user_repository import UserRepository
from store_service.service.errors import ConflictError, ForbiddenError, InternalError, NotFoundError
from store_service.service.store_service import StoreService
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, duplicate_key, not_found

STORE_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
LOGO_URL = "https://example.com/logo.png"


def new_service(ctrl: Controller) -> tuple[StoreService, StoreRepository, UserRepository]:
    store_repo = ctrl.mock(StoreRepository)
    user_repo = ctrl.mock(UserRepository)
    return StoreService(store_repo, user_repo), store_repo, user_repo


def owned_store() -> Store:
    return Store(id=STORE_ID, user_id=OWNER_ID, name="My Store")


class TestCreateStore:
    req = CreateStoreRequest(name="My Store", description="A test store")

    async def test_success(self, ctrl: Controller) -> None:
        svc, store_repo, user_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, OWNER_ID, raises=not_found())
        ctrl.expect(store_repo.create, ANY)
        ctrl.expect(user_repo.update_role, OWNER_ID, constant.Role.SELLER)

        resp = await svc.create_store(OWNER_ID, self.req)

        assert resp.name == self.req.name
        assert resp.user_id == OWNER_ID

    async def test_user_already_has_a_store(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, OWNER_ID, returns=owned_store())

        with pytest.raises(ConflictError, match="user already has a store"):
            await svc.create_store(OWNER_ID, self.req)

    async def test_create_fails(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, OWNER_ID, raises=not_found())
        ctrl.expect(store_repo.create, ANY, raises=db_error())

        with pytest.raises(InternalError, match="failed to create store"):
            await svc.create_store(OWNER_ID, self.req)

    async def test_concurrent_create_for_the_same_user(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, OWNER_ID, raises=not_found())
        ctrl.expect(store_repo.create, ANY, raises=duplicate_key())

        with pytest.raises(ConflictError, match="user already has a store"):
            await svc.create_store(OWNER_ID, self.req)

    async def test_update_role_fails_and_the_store_is_rolled_back(self, ctrl: Controller) -> None:
        svc, store_repo, user_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, OWNER_ID, raises=not_found())
        ctrl.expect(store_repo.create, ANY)
        ctrl.expect(user_repo.update_role, OWNER_ID, constant.Role.SELLER, raises=db_error())
        ctrl.expect(store_repo.delete, ANY)

        with pytest.raises(InternalError, match="failed to create store"):
            await svc.create_store(OWNER_ID, self.req)


class TestGetStoreByID:
    async def test_success(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())

        assert (await svc.get_store_by_id(STORE_ID)).id == STORE_ID

    async def test_store_not_found(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="store not found"):
            await svc.get_store_by_id(STORE_ID)


class TestUpdateStore:
    req = UpdateStoreRequest(name="Updated Store", description="Updated desc")

    async def test_success(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())
        ctrl.expect(store_repo.update, ANY)

        resp = await svc.update_store(OWNER_ID, STORE_ID, self.req)

        assert resp.name == "Updated Store"
        assert resp.description == "Updated desc"

    async def test_store_not_found(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="store not found"):
            await svc.update_store(OWNER_ID, STORE_ID, self.req)

    async def test_not_store_owner(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())

        with pytest.raises(ForbiddenError, match="forbidden"):
            await svc.update_store(OTHER_USER_ID, STORE_ID, self.req)

    async def test_update_fails(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())
        ctrl.expect(store_repo.update, ANY, raises=db_error())

        with pytest.raises(InternalError, match="failed to update store"):
            await svc.update_store(OWNER_ID, STORE_ID, self.req)


class TestUpdateLogo:
    async def test_success(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())
        ctrl.expect(store_repo.update, ANY)

        assert (await svc.update_logo(OWNER_ID, STORE_ID, LOGO_URL)).logo_url == LOGO_URL

    async def test_store_not_found(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="store not found"):
            await svc.update_logo(OWNER_ID, STORE_ID, LOGO_URL)

    async def test_not_store_owner(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())

        with pytest.raises(ForbiddenError, match="forbidden"):
            await svc.update_logo(OTHER_USER_ID, STORE_ID, LOGO_URL)

    async def test_update_fails(self, ctrl: Controller) -> None:
        svc, store_repo, _ = new_service(ctrl)
        ctrl.expect(store_repo.find_by_id, STORE_ID, returns=owned_store())
        ctrl.expect(store_repo.update, ANY, raises=db_error())

        with pytest.raises(InternalError, match="failed to update store logo"):
            await svc.update_logo(OWNER_ID, STORE_ID, LOGO_URL)
