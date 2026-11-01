from typing import Annotated
from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from .db import session as session_mod
from .db import queries as q
from .db.models import User
from .lib.auth import verify_bearer
from .lib.web_session import COOKIE_NAME, verify_session


def get_db():
    s = session_mod.SessionLocal()
    try: yield s
    finally: s.close()


def current_user(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    cp_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> User:
    user: User | None = None
    if authorization:
        token = verify_bearer(authorization)
        user = q.find_user_by_token(db, token)
    elif cp_session:
        user_id = verify_session(cp_session)
        user = db.get(User, user_id) if user_id else None
    else:
        raise HTTPException(status_code=401, detail="Authorization header or session cookie required")
    if not user:
        raise HTTPException(status_code=403, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")
    return user


def current_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return user
