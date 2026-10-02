import datetime as dt
import uuid
from dataclasses import dataclass

from store_service.model.zero import NIL_UUID, ZERO_TIME


@dataclass
class Category:
    id: uuid.UUID = NIL_UUID
    name: str = ""
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    def to_response(self) -> "CategoryResponse":
        return CategoryResponse(id=self.id, name=self.name, created_at=self.created_at, updated_at=self.updated_at)


@dataclass
class CreateCategoryRequest:
    name: str = ""


@dataclass
class UpdateCategoryRequest:
    name: str = ""


@dataclass
class CategoryResponse:
    id: uuid.UUID
    name: str
    created_at: dt.datetime
    updated_at: dt.datetime
