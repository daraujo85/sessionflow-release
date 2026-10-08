"""Authentication helpers: session JWT (HS256) creation/verification and a
small helper to extract a bearer token from a request.

Single-user model: the JWT ``sub`` is the account email. The middleware in
``app.main`` enforces a valid token on every route except the public auth
endpoints and ``GET /health``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Request

_ALGORITHM = "HS256"


def create_token(sub: str, *, secret: str, ttl_seconds: int) -> str:
    """Sign an HS256 JWT with ``sub`` and an ``exp`` ``ttl_seconds`` in the future."""
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, *, secret: str) -> dict | None:
    """Decode/validate a JWT (signature + exp). Returns the claims or ``None``."""
    if not token:
        return None
    try:
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None


def extract_token(request: Request) -> str | None:
    """Pull the bearer token from the request.

    Accepts ``Authorization: Bearer <jwt>`` or, for clients that cannot send
    headers (e.g. the SSE/EventSource stream), a ``?token=<jwt>`` query param.
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    token = request.query_params.get("token")
    if token:
        return token.strip()
    return None
