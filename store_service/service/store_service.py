import uuid

import structlog

from store_service import constant
from store_service.model import CreateStoreRequest, Store, StoreResponse, UpdateStoreRequest
from store_service.repository.errors import DuplicateKeyError
from store_service.repository.store_repository import StoreRepository
from store_service.repository.user_repository import UserRepository
from store_service.service.errors import ConflictError, ForbiddenError, InternalError, NotFoundError

log = structlog.get_logger()


class StoreService:
    def __init__(self, store_repo: StoreRepository, user_repo: UserRepository) -> None:
        self._store_repo = store_repo
        self._user_repo = user_repo

    async def create_store(self, user_id: uuid.UUID, req: CreateStoreRequest) -> StoreResponse:
        try:
            await self._store_repo.find_by_user_id(user_id)
        except Exception:  # noqa: BLE001 - a failed lookup counts as no store, as in the Go service
            pass
        else:
            raise ConflictError("user already has a store")

        store = Store(user_id=user_id, name=req.name, description=req.description)

        try:
            await self._store_repo.create(store)
        except DuplicateKeyError as e:
            # A concurrent request created this user's store after the check above.
            raise ConflictError("user already has a store") from e
        except Exception as e:
            log.error("failed to create store", error=str(e))
            raise InternalError("failed to create store") from e

        try:
            await self._user_repo.update_role(user_id, constant.Role.SELLER)
        except Exception as e:
            try:
                await self._store_repo.delete(store.id)
            except Exception as rb_err:  # noqa: BLE001
                log.error(
                    "failed to rollback store after role update failure", error=str(rb_err), store_id=str(store.id)
                )
            log.error("failed to update user role", error=str(e))
            raise InternalError("failed to create store") from e

        log.info("store created", store_id=str(store.id), user_id=str(user_id))

        return store.to_response()

    async def get_store_by_id(self, store_id: uuid.UUID) -> StoreResponse:
        return (await self._find_store(store_id)).to_response()

    async def update_store(self, user_id: uuid.UUID, store_id: uuid.UUID, req: UpdateStoreRequest) -> StoreResponse:
        store = await self._find_store(store_id)

        if store.user_id != user_id:
            raise ForbiddenError("forbidden: not store owner")

        if req.name:
            store.name = req.name
        if req.description:
            store.description = req.description

        try:
            await self._store_repo.update(store)
        except Exception as e:
            log.error("failed to update store", error=str(e))
            raise InternalError("failed to update store") from e

        return store.to_response()

    async def update_logo(self, user_id: uuid.UUID, store_id: uuid.UUID, logo_url: str) -> StoreResponse:
        store = await self._find_store(store_id)

        if store.user_id != user_id:
            raise ForbiddenError("forbidden: not store owner")

        store.logo_url = logo_url
        try:
            await self._store_repo.update(store)
        except Exception as e:
            log.error("failed to update store logo", error=str(e))
            raise InternalError("failed to update store logo") from e

        return store.to_response()

    async def _find_store(self, store_id: uuid.UUID) -> Store:
        try:
            return await self._store_repo.find_by_id(store_id)
        except Exception as e:
            raise NotFoundError("store not found") from e
