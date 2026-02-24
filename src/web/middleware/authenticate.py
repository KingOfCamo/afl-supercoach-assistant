from __future__ import annotations

"""JWT authentication dependency for FastAPI routes."""

import os

import jwt
from fastapi import Depends, HTTPException, Request


JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: extract and verify JWT from Authorization header.

    Returns dict with user_id and email on success.
    Raises 401 if token is missing, expired, or invalid.
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication token")

    token = auth_header[7:]  # Strip "Bearer "

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {"user_id": payload["user_id"], "email": payload["email"]}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid token")
