import json
import os
import secrets
import shutil
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db.models import DiskState, JobEvent, User
from ..deps import get_db
from ..lib import encryption

router = APIRouter(prefix="/admin")


def require_admin(x_admin_token: Annotated[str | None, Header()] = None):
    expected = os.environ.get("PIPELINE_ADMIN_TOKEN")
    if not expected or x_admin_token != expected:
        raise HTTPException(401, "admin token required")


class CreateUserBody(BaseModel):
    name: str
    context_summary: str | None = None
    system_prompt_override: str | None = None


@router.post("/users", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
def add_user(body: CreateUserBody, db: Annotated[Session, Depends(get_db)]):
    uid = str(uuid.uuid4())
    bearer = secrets.token_urlsafe(32)
    u = User(
        id=uid, name=body.name, bearer_token=bearer,
        context_summary=body.context_summary,
        system_prompt_override=body.system_prompt_override,
    )
    db.add(u)
    db.commit()
    return {"id": uid, "name": body.name, "bearer_token": bearer}


@router.post("/users/{user_id}/rotate-token",
             dependencies=[Depends(require_admin)])
def rotate_token(user_id: str, db: Annotated[Session, Depends(get_db)]):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    new = secrets.token_urlsafe(32)
    u.bearer_token = new
    db.commit()
    return {"bearer_token": new}


@router.get("/disk-status", dependencies=[Depends(require_admin)])
def disk_status(db: Annotated[Session, Depends(get_db)]):
    state = db.get(DiskState, 1)
    if state is None:
        u = shutil.disk_usage("/")
        return {
            "usage_pct": u.used / u.total * 100,
            "free_gb": u.free / 1024**3,
            "warning": False,
            "snapshot": "live",
        }
    return {
        "usage_pct": state.usage_pct,
        "free_gb": state.free_gb,
        "warning": state.warning,
        "snapshot": state.updated_at.isoformat(),
    }


@router.get("/jobs/{job_id}/events", dependencies=[Depends(require_admin)])
def tail_logs(job_id: str, db: Annotated[Session, Depends(get_db)]):
    events = db.scalars(
        select(JobEvent)
        .where(JobEvent.job_id == job_id)
        .order_by(JobEvent.created_at)
    ).all()
    return {
        "events": [
            {
                "event": e.event, "stage": e.stage,
                "duration_ms": e.duration_ms,
                "error": e.error,
                "at": e.created_at.isoformat(),
            }
            for e in events
        ]
    }
