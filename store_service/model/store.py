import datetime as dt
import uuid
from dataclasses import dataclass

from store_service.model.zero import NIL_UUID, ZERO_TIME


@dataclass
class Store:
    id: uuid.UUID = NIL_UUID
    user_id: uuid.UUID = NIL_UUID
    name: str = ""
    description: str = ""
    logo_url: str = ""
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    def to_response(self) -> "StoreResponse":
        return StoreResponse(
            id=self.id,
            user_id=self.user_id,
            name=self.name,
            description=self.description,
            logo_url=self.logo_url,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class CreateStoreRequest:
    name: str = ""
    description: str = ""


@dataclass
class UpdateStoreRequest:
    name: str = ""
    description: str = ""


@dataclass
class StoreResponse:
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    description: str
    logo_url: str
    created_at: dt.datetime
    updated_at: dt.datetime
