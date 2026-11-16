"""Cognito access-token verification for the read API.

The dashboard already sends `Authorization: Bearer <idToken>` on every request
(`frontend/src/services/apiClient.ts`), but nothing checked it, because there
was no server. This endpoint serves a trader's positions and accepts approval
writes, so the token is verified here rather than trusted.

Verification is enabled by setting `COGNITO_USER_POOL_ID`. With no pool
configured the dependency allows every request and logs a warning on startup:
that is the local-development path, and it is deliberately loud so an
unprotected deployment is visible in the logs rather than silent.
"""

import logging
import os
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-east-1")

# Cognito rotates signing keys rarely; refetching the key set on every request
# would add a round trip to each call for no benefit.
_JWKS_TTL_SECONDS = 3600
_jwks_cache = {"keys": None, "fetched_at": 0.0}

# auto_error=False so an anonymous request reaches the dependency and is handled
# there. With the default, a missing header 403s before the local-development
# path can allow it.
_bearer = HTTPBearer(auto_error=False)


def auth_required() -> bool:
    return bool(COGNITO_USER_POOL_ID)


def _issuer() -> str:
    return f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"


def _load_jwks():
    now = time.time()
    if _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS:
        return _jwks_cache["keys"]

    import requests

    response = requests.get(f"{_issuer()}/.well-known/jwks.json", timeout=5)
    response.raise_for_status()
    keys = response.json().get("keys", [])

    _jwks_cache["keys"] = keys
    _jwks_cache["fetched_at"] = now
    return keys


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_token(credentials: HTTPAuthorizationCredentials):
    """Validate signature, expiry and issuer. Returns the token claims."""
    try:
        import jwt
        from jwt import PyJWKClient  # noqa: F401  (import proves crypto extras are present)
    except ImportError:
        # Failing open here would mean an unverified token is treated as valid.
        logger.error("PyJWT is not installed; cannot verify tokens.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token verification is unavailable.",
        )

    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        raise _unauthorized("Malformed token.")

    kid = header.get("kid")
    key_data = next((key for key in _load_jwks() if key.get("kid") == kid), None)
    if key_data is None:
        raise _unauthorized("Token signing key is not recognised.")

    try:
        signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            issuer=_issuer(),
            # An id token carries the client id in `aud`; an access token does
            # not, and carries it in `client_id` instead. The audience check is
            # therefore applied below against whichever claim is present.
            options={"verify_aud": False},
        )
    except Exception as e:
        logger.info("Rejected token: %s", type(e).__name__)
        raise _unauthorized("Invalid or expired token.")

    if COGNITO_CLIENT_ID:
        presented = claims.get("aud") or claims.get("client_id")
        if presented != COGNITO_CLIENT_ID:
            raise _unauthorized("Token was not issued for this application.")

    return claims


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
):
    """FastAPI dependency guarding every data route."""
    if not auth_required():
        return {"sub": "local-development", "unverified": True}

    if credentials is None or not credentials.credentials:
        raise _unauthorized("Missing bearer token.")

    claims = verify_token(credentials)
    request.state.user = claims
    return claims
