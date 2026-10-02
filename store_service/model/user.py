import datetime as dt
import uuid
from dataclasses import dataclass, field

from common import gojson
from store_service.constant import Role
from store_service.model.zero import NIL_UUID, ZERO_TIME


@dataclass
class User:
    id: uuid.UUID = NIL_UUID
    email: str = ""
    password: str = field(default="", metadata=gojson.SKIP)
    name: str = ""
    role: Role = Role.BUYER
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    def to_response(self) -> "UserResponse":
        return UserResponse(
            id=self.id,
            email=self.email,
            name=self.name,
            role=self.role,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class RegisterRequest:
    email: str = ""
    password: str = ""
    name: str = ""


@dataclass
class LoginRequest:
    email: str = ""
    password: str = ""


@dataclass
class RefreshRequest:
    refresh_token: str = ""


@dataclass
class UserResponse:
    id: uuid.UUID
    email: str
    name: str
    role: Role
    created_at: dt.datetime
    updated_at: dt.datetime
