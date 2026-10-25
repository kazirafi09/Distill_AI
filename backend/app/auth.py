from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import (
    create_access_token,
    decode_access_identity,
    hash_password,
    verify_password,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer = HTTPBearer(auto_error=False)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

DUMMY_HASH = hash_password("dummy-password-for-timing-only")


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class SignupRequest(LoginRequest):
    password: SecretStr = Field(min_length=12, max_length=1024)


SESSION_COOKIE = "distill_session"


def local_browser_origin(request: Request) -> bool:
    try:
        origin = urlsplit(request.headers.get("origin", ""))
    except ValueError:
        raise HTTPException(403, "Request origin is not allowed") from None
    return (
        origin.scheme in {"http", "https"}
        and origin.hostname in {"localhost", "127.0.0.1"}
        and request.url.hostname in {"localhost", "127.0.0.1"}
    )


def check_origin(request: Request) -> None:
    """Cookie-authenticated actions must originate from the configured app."""
    origin = request.headers.get("origin")
    local = local_browser_origin(request)
    if origin and not local and origin.rstrip("/") != get_settings().public_app_url.rstrip("/"):
        raise HTTPException(403, "Request origin is not allowed")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site authentication request is not allowed")


def cookie_options(request: Request):
    local_http = local_browser_origin(request) and request.headers.get("origin", "").startswith("http://")
    return {
        "httponly": True,
        "secure": not local_http and urlsplit(get_settings().public_app_url).scheme == "https",
        "samesite": "lax",
        "path": "/",
    }


def session_response(user: User, session: AuthSession, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": create_access_token(user.id, session.id), "token_type": "bearer"}


async def open_session(user: User, request: Request, response: Response, db: AsyncSession):
    # Replace this browser's previous session without affecting other devices.
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        await db.execute(delete(AuthSession).where(
            AuthSession.token_hash == sha256(previous.encode()).hexdigest(),
        ))
    await db.execute(delete(AuthSession).where(AuthSession.expires_at <= datetime.now(timezone.utc)))
    token = token_urlsafe(48)
    lifetime = timedelta(days=get_settings().auth_session_days)
    session = AuthSession(
        user_id=user.id,
        token_hash=sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + lifetime,
    )
    db.add(session)
    await db.commit()
    response.set_cookie(SESSION_COOKIE, token, max_age=int(lifetime.total_seconds()), **cookie_options(request))
    return session_response(user, session, response)


@router.post("/signup", status_code=201)
async def signup(body: SignupRequest, request: Request, response: Response, db: DatabaseSession):
    check_origin(request)
    email = str(body.email).strip().lower()
    user = User(
        email=email, role="recruiter", is_active=True,
        password_hash=await run_in_threadpool(hash_password, body.password.get_secret_value()),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "An account with this email already exists. Please sign in.") from None
    return await open_session(user, request, response, db)


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid credentials or access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: DatabaseSession):
    check_origin(request)
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

    return await open_session(user, request, response, db)


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: DatabaseSession):
    check_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise authentication_error()
    session = await db.scalar(select(AuthSession).where(
        AuthSession.token_hash == sha256(token.encode()).hexdigest(),
        AuthSession.expires_at > datetime.now(timezone.utc),
    ))
    user = await db.get(User, session.user_id) if session else None
    if not user or not user.is_active:
        raise authentication_error()
    return session_response(user, session, response)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DatabaseSession):
    check_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.execute(delete(AuthSession).where(
            AuthSession.token_hash == sha256(token.encode()).hexdigest(),
        ))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, **cookie_options(request))
    response.headers["Cache-Control"] = "no-store"


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
        user_id, session_id = decode_access_identity(credentials.credentials)
    except InvalidTokenError:
        raise authentication_error() from None

    user = await db.get(User, user_id)

    if not user or not user.is_active:
        raise authentication_error()

    if session_id is not None:
        session = await db.get(AuthSession, session_id)
        if (not session or session.user_id != user.id
                or session.expires_at <= datetime.now(timezone.utc)):
            raise authentication_error()

    # Authentication is read-only. Return the connection before the endpoint
    # waits on a model, stream or another service; loaded user fields stay valid.
    await db.commit()
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
async def current_user(user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
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
