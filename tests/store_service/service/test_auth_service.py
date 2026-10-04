import datetime as dt
import uuid
from unittest.mock import ANY

import bcrypt
import pytest

from common.jwt import JWTManager
from store_service import constant
from store_service.model import LoginRequest, RefreshRequest, RegisterRequest, User
from store_service.repository.user_repository import UserRepository
from store_service.service.auth_service import AuthService
from store_service.service.errors import ConflictError, InternalError, UnauthorizedError
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, duplicate_key, not_found

HASHED_PASSWORD = bcrypt.hashpw(b"password123", bcrypt.gensalt(rounds=4)).decode()


def new_jwt_manager() -> JWTManager:
    return JWTManager("test-secret-of-at-least-thirty-two-bytes", dt.timedelta(minutes=15), dt.timedelta(hours=168))


def new_service(ctrl: Controller, jwt_manager: JWTManager | None = None) -> tuple[AuthService, UserRepository]:
    repo = ctrl.mock(UserRepository)
    return AuthService(repo, jwt_manager or new_jwt_manager()), repo


REGISTER = RegisterRequest(email="test@example.com", password="password123", name="Test User")


class TestRegister:
    async def test_success(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_email, "test@example.com", raises=not_found())
        ctrl.expect(repo.create, ANY)

        resp = await svc.register(REGISTER)

        assert resp.email == REGISTER.email
        assert resp.name == REGISTER.name
        assert resp.role == constant.Role.BUYER

    async def test_email_already_registered(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_email, "test@example.com", returns=User(id=uuid.uuid4(), email="test@example.com"))

        with pytest.raises(ConflictError, match="email already registered"):
            await svc.register(REGISTER)

    async def test_create_user_fails(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_email, "test@example.com", raises=not_found())
        ctrl.expect(repo.create, ANY, raises=db_error())

        with pytest.raises(InternalError, match="failed to create user"):
            await svc.register(REGISTER)

    async def test_concurrent_registration_with_same_email(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_email, "test@example.com", raises=not_found())
        ctrl.expect(repo.create, ANY, raises=duplicate_key())

        with pytest.raises(ConflictError, match="email already registered"):
            await svc.register(REGISTER)

    async def test_password_is_stored_as_a_go_compatible_bcrypt_hash(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        created: list[User] = []
        ctrl.expect(repo.find_by_email, "test@example.com", raises=not_found())
        ctrl.expect(repo.create, ANY, does=created.append)

        await svc.register(REGISTER)

        # The Go service writes $2a$ hashes at cost 10; both services must read each other's.
        assert created[0].password.startswith("$2a$10$")
        assert bcrypt.checkpw(b"password123", created[0].password.encode())


class TestLogin:
    async def test_success(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        user = User(id=uuid.uuid4(), email="test@example.com", password=HASHED_PASSWORD, role=constant.Role.BUYER)
        ctrl.expect(repo.find_by_email, "test@example.com", returns=user)

        pair = await svc.login(LoginRequest(email="test@example.com", password="password123"))

        assert pair.access_token
        assert pair.refresh_token

    async def test_invalid_email(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        ctrl.expect(repo.find_by_email, "wrong@example.com", raises=not_found())

        with pytest.raises(UnauthorizedError, match="invalid email or password"):
            await svc.login(LoginRequest(email="wrong@example.com", password="password123"))

    async def test_wrong_password(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        user = User(id=uuid.uuid4(), email="test@example.com", password=HASHED_PASSWORD, role=constant.Role.BUYER)
        ctrl.expect(repo.find_by_email, "test@example.com", returns=user)

        with pytest.raises(UnauthorizedError, match="invalid email or password"):
            await svc.login(LoginRequest(email="test@example.com", password="wrongpassword"))

    async def test_accepts_a_hash_made_by_the_go_service(self, ctrl: Controller) -> None:
        # golang.org/x/crypto/bcrypt, cost 10, for "password123".
        go_hash = "$2a$10$Qmp5JmkBck6XB29M0eBpu.3XInbu11Vw0oGtdJY2d1.cRBlXXrUiS"
        svc, repo = new_service(ctrl)
        user = User(id=uuid.uuid4(), email="test@example.com", password=go_hash, role=constant.Role.BUYER)
        ctrl.expect(repo.find_by_email, "test@example.com", returns=user)

        assert await svc.login(LoginRequest(email="test@example.com", password="password123"))


class TestRefreshToken:
    user_id = uuid.uuid4()
    jwt_manager = new_jwt_manager()
    pair = jwt_manager.generate_token_pair(str(user_id), "test@example.com", constant.Role.BUYER)

    async def test_success_role_reloaded_from_database(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl, self.jwt_manager)
        user = User(id=self.user_id, email="test@example.com", role=constant.Role.SELLER)
        ctrl.expect(repo.find_by_id, self.user_id, returns=user)

        pair = await svc.refresh_token(RefreshRequest(refresh_token=self.pair.refresh_token))

        assert self.jwt_manager.validate_token(pair.access_token).role == constant.Role.SELLER

    @pytest.mark.parametrize("token", [pair.access_token, "not-a-token"], ids=["access token", "malformed token"])
    async def test_rejected_token(self, ctrl: Controller, token: str) -> None:
        svc, _ = new_service(ctrl, self.jwt_manager)

        with pytest.raises(UnauthorizedError, match="invalid refresh token"):
            await svc.refresh_token(RefreshRequest(refresh_token=token))

    async def test_user_no_longer_exists(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl, self.jwt_manager)
        ctrl.expect(repo.find_by_id, self.user_id, raises=not_found())

        with pytest.raises(UnauthorizedError, match="invalid refresh token"):
            await svc.refresh_token(RefreshRequest(refresh_token=self.pair.refresh_token))


class TestEmailIsCaseInsensitive:
    async def test_register_stores_the_email_in_lower_case(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        created: list[User] = []
        ctrl.expect(repo.find_by_email, "andreas@example.com", raises=not_found())
        ctrl.expect(repo.create, ANY, does=created.append)

        resp = await svc.register(RegisterRequest(email="Andreas@Example.COM", password="password123", name="Andreas"))

        assert resp.email == "andreas@example.com"
        assert created[0].email == "andreas@example.com"

    async def test_register_rejects_a_case_variant_of_an_existing_email(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        existing = User(id=uuid.uuid4(), email="andreas@example.com")
        ctrl.expect(repo.find_by_email, "andreas@example.com", returns=existing)

        with pytest.raises(ConflictError, match="email already registered"):
            await svc.register(RegisterRequest(email="ANDREAS@example.com", password="password123", name="Andreas"))

    async def test_login_matches_regardless_of_case(self, ctrl: Controller) -> None:
        svc, repo = new_service(ctrl)
        user = User(id=uuid.uuid4(), email="andreas@example.com", password=HASHED_PASSWORD, role=constant.Role.BUYER)
        ctrl.expect(repo.find_by_email, "andreas@example.com", returns=user)

        assert await svc.login(LoginRequest(email="Andreas@Example.com", password="password123"))
