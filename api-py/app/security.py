"""JWT-based authentication against Keycloak.

Exports `CurrentUserDep`, a FastAPI dependency that validates a
`Authorization: Bearer <token>` header against the configured Keycloak
issuer and returns the principal. Route handlers protect themselves
simply by declaring `user: CurrentUserDep` as a parameter.
"""

from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.config import get_settings

logger = logging.getLogger(__name__)

# auto_error=False so we control the 401 shape ourselves — FastAPI's
# default error body is `{"detail": "Not authenticated"}` which is hard
# to distinguish from other 401s.
_bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    """The authenticated principal making a request.

    Every field here is derived from the validated JWT. Don't trust any
    user information that didn't come from the token.
    """

    model_config = ConfigDict(frozen=True)

    sub: str
    username: str
    email: str | None = None
    name: str | None = None


def _get_jwks_client(request: Request) -> jwt.PyJWKClient:
    """Lazily build and cache the JWKS client on app.state.

    PyJWKClient handles HTTP fetch + key caching + automatic refetch on
    `kid` miss (which happens when Keycloak rotates signing keys).
    """
    client = getattr(request.app.state, "jwks_client", None)
    if client is None:
        settings = get_settings()
        client = jwt.PyJWKClient(
            settings.keycloak_jwks_url,
            cache_jwk_set=True,
            lifespan=3600,
        )
        request.app.state.jwks_client = client
    return client


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    jwks_client = _get_jwks_client(request)
    token = credentials.credentials

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer,
            # Skip audience check — Keycloak's default `aud` is "account"
            # for password-grant tokens, which isn't useful for our
            # purposes. We rely on `iss` + signature instead. Tighten
            # later if/when we set up explicit audience mappers.
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Wrong token issuer",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        # Covers signature mismatch, malformed token, missing required
        # claim, etc. Log the reason for debugging — don't leak it to
        # the client.
        logger.warning("JWT validation failed: %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        sub=claims["sub"],
        username=claims.get("preferred_username", claims["sub"]),
        email=claims.get("email"),
        name=claims.get("name"),
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
