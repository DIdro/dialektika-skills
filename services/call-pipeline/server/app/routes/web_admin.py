"""Админ-панель (веб): управление пользователями. Доступ — is_admin, cookie или bearer.

Отдельно от старого /admin (X-Admin-Token) — тот остаётся для скриптов/CLI.
"""
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Job, Recording, User
from ..deps import current_admin, get_db
from ..lib.passwords import hash_password

router = APIRouter(prefix="/web/admin", dependencies=[Depends(current_admin)])


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _email_taken(db: Session, email: str, *, exclude_id: str | None = None) -> bool:
    stmt = select(User.id).where(User.email == email)
    if exclude_id:
        stmt = stmt.where(User.id != exclude_id)
    return db.scalar(stmt) is not None


@router.get("/users")
def list_users(db: Annotated[Session, Depends(get_db)]):
    jobs_c = dict(db.execute(
        select(Job.user_id, func.count()).group_by(Job.user_id)).all())
    recs_c = dict(db.execute(
        select(Recording.user_id, func.count()).group_by(Recording.user_id)).all())
    users = db.scalars(select(User).order_by(User.created_at)).all()
    return {"users": [{
        "id": u.id, "name": u.name, "email": u.email,
        "is_admin": u.is_admin, "is_active": u.is_active,
        "jobs_count": jobs_c.get(u.id, 0),
        "recordings_count": recs_c.get(u.id, 0),
        "created_at": u.created_at.isoformat(),
    } for u in users]}


class CreateUserBody(BaseModel):
    name: str
    email: str


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: CreateUserBody, db: Annotated[Session, Depends(get_db)]):
    email = _norm_email(body.email)
    if _email_taken(db, email):
        raise HTTPException(409, "email already in use")
    temp_password = secrets.token_urlsafe(9)
    u = User(
        id=str(uuid.uuid4()), name=body.name.strip(), email=email,
        bearer_token=secrets.token_urlsafe(32),
        password_hash=hash_password(temp_password),
    )
    db.add(u)
    db.commit()
    return {"id": u.id, "email": email,
            "temp_password": temp_password, "bearer_token": u.bearer_token}


def _get_user_or_404(db: Session, user_id: str) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    return u


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, db: Annotated[Session, Depends(get_db)]):
    u = _get_user_or_404(db, user_id)
    temp_password = secrets.token_urlsafe(9)
    u.password_hash = hash_password(temp_password)
    db.commit()
    return {"temp_password": temp_password}


class SetActiveBody(BaseModel):
    active: bool


@router.post("/users/{user_id}/set-active")
def set_active(user_id: str, body: SetActiveBody,
               db: Annotated[Session, Depends(get_db)]):
    u = _get_user_or_404(db, user_id)
    u.is_active = body.active
    db.commit()
    return {"ok": True}


class SetEmailBody(BaseModel):
    email: str


@router.post("/users/{user_id}/set-email")
def set_email(user_id: str, body: SetEmailBody,
              db: Annotated[Session, Depends(get_db)]):
    u = _get_user_or_404(db, user_id)
    email = _norm_email(body.email)
    if _email_taken(db, email, exclude_id=user_id):
        raise HTTPException(409, "email already in use")
    u.email = email
    db.commit()
    return {"ok": True}
