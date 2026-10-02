from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer = HTTPBearer(auto_error=False)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

DUMMY_HASH = hash_password("dummy-password-for-timing-only")


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid credentials or access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login")
async def login(body: LoginRequest, db: DatabaseSession):
    email = str(body.email).strip().lower()

    user = await db.scalar(
        select(User).where(User.email == email)
    )

    stored_hash = user.password_hash if user else DUMMY_HASH
    password_valid = await run_in_threadpool(
        verify_password,
        body.password.get_secret_value(),
        stored_hash,
    )

    if not user or not password_valid or not user.is_active:
        raise authentication_error()

    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
    }


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
    db: DatabaseSession,
) -> User:
    if credentials is None:
        raise authentication_error()

    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise authentication_error() from None

    user = await db.get(User, user_id)

    if not user or not user.is_active:
        raise authentication_error()

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_recruiter(user: CurrentUser) -> User:
    if user.role != "recruiter":
        raise HTTPException(
            status_code=403,
            detail="Recruiter access required",
        )

    return user


@router.get("/me")
async def current_user(user: CurrentUser):
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
    }


@router.get("/recruiter-check")
async def recruiter_check(
    user: Annotated[User, Depends(require_recruiter)],
):
    return {"status": "ok", "role": user.role}