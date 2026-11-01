"""Веб-аутентификация: email+пароль → httpOnly cookie-сессия.

Bearer-путь CLI/ботов живёт отдельно (deps.current_user принимает оба).
Rate-limit на login — in-memory (один процесс API, этого достаточно).
"""
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import User
from ..deps import current_user, get_db
from ..lib.passwords import hash_password, verify_password
from ..lib.web_session import COOKIE_NAME, MAX_AGE_SEC, issue_session

router = APIRouter(prefix="/web")

_RATE_WINDOW_SEC = 60
_RATE_MAX_ATTEMPTS = 5
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # За nginx — реальный IP в X-Real-IP; напрямую — request.client
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    dq = _attempts[ip]
    while dq and now - dq[0] > _RATE_WINDOW_SEC:
        dq.popleft()
    if len(dq) >= _RATE_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many login attempts, try again in a minute")
    dq.append(now)


# Фиктивный bcrypt-хэш: verify_password против него сжигает столько же времени,
# сколько реальная проверка, чтобы несуществующий email нельзя было отличить от
# существующего по времени ответа (защита от перебора email по таймингу).
_DUMMY_HASH = hash_password("timing-equalizer")


def _user_view(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "is_admin": u.is_admin}


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response,
          db: Annotated[Session, Depends(get_db)]):
    _check_rate_limit(_client_ip(request))
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    # Всегда прогоняем bcrypt (против реального либо фиктивного хэша), чтобы время
    # ответа не выдавало существование email.
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not user.is_active or not password_ok:
        raise HTTPException(401, "Invalid email or password")
    response.set_cookie(
        COOKIE_NAME, issue_session(user.id),
        max_age=MAX_AGE_SEC, httponly=True, secure=True, samesite="lax", path="/",
    )
    return _user_view(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]):
    return _user_view(user)


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordBody,
                    user: Annotated[User, Depends(current_user)],
                    db: Annotated[Session, Depends(get_db)]):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(403, "Old password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    db_user = db.get(User, user.id)
    db_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
