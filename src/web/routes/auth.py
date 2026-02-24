from __future__ import annotations

"""Authentication endpoints: register and login."""

import os
from datetime import datetime, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from src.models.database import User, get_session

router = APIRouter()

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_DAYS = 7


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _create_token(user: User) -> str:
    payload = {
        "user_id": user.id,
        "email": user.email,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRES_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@router.post("/register")
def register(request: RegisterRequest) -> dict:
    """Create a new user account."""
    if not request.email or not request.password or not request.display_name:
        raise HTTPException(status_code=400, detail="All fields are required")

    if len(request.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    session = get_session()
    try:
        # Check if email already exists
        existing = session.execute(
            select(User).where(User.email == request.email.lower())
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        user = User(
            email=request.email.lower().strip(),
            password_hash=_hash_password(request.password),
            display_name=request.display_name.strip(),
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        token = _create_token(user)
        return {
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/login")
def login(request: LoginRequest) -> dict:
    """Authenticate and return JWT token."""
    if not request.email or not request.password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    session = get_session()
    try:
        user = session.execute(
            select(User).where(User.email == request.email.lower())
        ).scalar_one_or_none()

        if not user or not _verify_password(request.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        token = _create_token(user)
        return {
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
