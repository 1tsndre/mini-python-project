import asyncio

import bcrypt
import structlog

from common.jwt import JWTManager, TokenPair, TokenType
from store_service import constant
from store_service.model import LoginRequest, RefreshRequest, RegisterRequest, User, UserResponse
from store_service.repository.errors import DuplicateKeyError
from store_service.repository.user_repository import UserRepository
from store_service.service.errors import ConflictError, InternalError, UnauthorizedError
from store_service.util import strings, uuids

log = structlog.get_logger()

# bcrypt's default cost in the Go service, and the bytes of a password it reads.
BCRYPT_COST = 10
BCRYPT_MAX_BYTES = 72


def normalize_email(email: str) -> str:
    """Emails are effectively case-insensitive (mobile keyboards capitalise the first letter), so an
    account registered as "Andreas@x.com" must be reachable as "andreas@x.com" and must not be
    registrable twice.
    """
    return strings.to_lower(email)


def _check_password(hashed: str, password: str) -> bool:
    try:
        # Like Go's bcrypt, bytes past the 72nd do not count.
        return bcrypt.checkpw(password.encode()[:BCRYPT_MAX_BYTES], hashed.encode())
    except ValueError:
        return False


class AuthService:
    def __init__(self, user_repo: UserRepository, jwt_manager: JWTManager) -> None:
        self._user_repo = user_repo
        self._jwt_manager = jwt_manager

    async def register(self, req: RegisterRequest) -> UserResponse:
        email = normalize_email(req.email)

        try:
            await self._user_repo.find_by_email(email)
        except Exception:  # noqa: BLE001 - like the Go service, a failed lookup counts as no account
            pass
        else:
            raise ConflictError("email already registered")

        try:
            salt = bcrypt.gensalt(rounds=BCRYPT_COST, prefix=b"2a")
            hashed_password = await asyncio.to_thread(bcrypt.hashpw, req.password.encode(), salt)
        except ValueError as e:
            log.error("failed to hash password", error=str(e))
            raise InternalError("internal server error") from e

        user = User(email=email, password=hashed_password.decode(), name=req.name, role=constant.Role.BUYER)

        try:
            await self._user_repo.create(user)
        except DuplicateKeyError as e:
            # A concurrent registration with the same email passed the check above.
            raise ConflictError("email already registered") from e
        except Exception as e:
            log.error("failed to create user", error=str(e))
            raise InternalError("failed to create user") from e

        log.info("user registered", user_id=str(user.id), email=user.email)

        return user.to_response()

    async def login(self, req: LoginRequest) -> TokenPair:
        try:
            user = await self._user_repo.find_by_email(normalize_email(req.email))
        except Exception as e:
            raise UnauthorizedError("invalid email or password") from e

        if not await asyncio.to_thread(_check_password, user.password, req.password):
            raise UnauthorizedError("invalid email or password")

        token_pair = self._generate_token_pair(user)

        log.info("user logged in", user_id=str(user.id))

        return token_pair

    async def refresh_token(self, req: RefreshRequest) -> TokenPair:
        try:
            claims = self._jwt_manager.validate_token(req.refresh_token)
        except Exception as e:
            raise UnauthorizedError("invalid refresh token") from e

        if claims.type != TokenType.REFRESH:
            raise UnauthorizedError("invalid refresh token")

        user_id = uuids.parse(claims.user_id)
        if user_id is None:
            raise UnauthorizedError("invalid refresh token")

        # Reload the user so the new tokens carry the current role (e.g. buyer -> seller after
        # creating a store) instead of the one baked into the old refresh token.
        try:
            user = await self._user_repo.find_by_id(user_id)
        except Exception as e:
            raise UnauthorizedError("invalid refresh token") from e

        return self._generate_token_pair(user)

    def _generate_token_pair(self, user: User) -> TokenPair:
        try:
            return self._jwt_manager.generate_token_pair(str(user.id), user.email, user.role)
        except Exception as e:
            log.error("failed to generate token pair", error=str(e))
            raise InternalError("internal server error") from e
