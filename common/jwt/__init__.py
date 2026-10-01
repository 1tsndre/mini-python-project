import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

import jwt as pyjwt


class TokenType(StrEnum):
    """Values of the type claim, so a refresh token cannot be used as an access token."""

    ACCESS = "access"
    REFRESH = "refresh"


# Like the Go service, any HMAC algorithm is accepted when validating.
_HMAC_ALGORITHMS = ["HS256", "HS384", "HS512"]
_CLAIMS = ("user_id", "email", "role", "type")


class InvalidTokenError(Exception):
    def __init__(self) -> None:
        super().__init__("invalid token")


class ExpiredTokenError(Exception):
    def __init__(self) -> None:
        super().__init__("token has expired")


@dataclass(frozen=True)
class Claims:
    user_id: str
    email: str
    role: str
    type: str


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str


class JWTManager:
    def __init__(self, secret: str, access_expiry: dt.timedelta, refresh_expiry: dt.timedelta) -> None:
        self._secret = secret.encode()
        self._access_expiry = access_expiry
        self._refresh_expiry = refresh_expiry

    def generate_token_pair(self, user_id: str, email: str, role: str) -> TokenPair:
        return TokenPair(
            access_token=self._generate_token(user_id, email, role, TokenType.ACCESS, self._access_expiry),
            refresh_token=self._generate_token(user_id, email, role, TokenType.REFRESH, self._refresh_expiry),
        )

    def validate_token(self, token: str) -> Claims:
        """Returns the claims of a valid token.

        Raises ExpiredTokenError if the token has expired, InvalidTokenError otherwise.
        """
        try:
            # Like the Go service, "exp" and "nbf" are checked but "iat" is not.
            payload = pyjwt.decode(token, self._secret, algorithms=_HMAC_ALGORITHMS, options={"verify_iat": False})
        except pyjwt.ExpiredSignatureError as e:
            raise ExpiredTokenError() from e
        except pyjwt.PyJWTError as e:
            raise InvalidTokenError() from e

        values = []
        for name in _CLAIMS:
            value = payload.get(name)
            if value is None:
                value = ""
            elif not isinstance(value, str):
                # A claim of another type does not unmarshal into the Go claims struct.
                raise InvalidTokenError()
            values.append(value)
        return Claims(*values)

    def _generate_token(self, user_id: str, email: str, role: str, token_type: TokenType, expiry: dt.timedelta) -> str:
        now = dt.datetime.now(dt.UTC)
        claims = {
            "user_id": user_id,
            "email": email,
            "role": role,
            "type": token_type,
            "exp": int((now + expiry).timestamp()),
            "iat": int(now.timestamp()),
        }
        return pyjwt.encode(claims, self._secret, algorithm="HS256")
