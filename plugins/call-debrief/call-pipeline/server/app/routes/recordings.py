"""Telemost recording API — the CLI/agent path.

`pipeline record <url>` POSTs here under the user's bearer; worker-recorder
runs the headless recorder, then submits a mode=call Job under the SAME
user_id, so the resulting draft lands in that user's the user's drafts (exactly
like `pipeline upload`, but the audio is captured server-side). The Telegram
bot keeps its own path (delivers to TG) untouched.
"""
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Recording, User
from ..deps import current_user, get_db

router = APIRouter()

# Accept consumer Telemost and Yandex 360 (telemost.<sub>.yandex.ru).
URL_RE = re.compile(r"https?://telemost\.(?:[a-z0-9-]+\.)?yandex\.ru/\S+", re.I)
ACTIVE = ("requested", "recording")


def _view(r: Recording) -> dict:
    return {
        "recording_id": r.id, "status": r.status, "url": r.url,
        "num_speakers": r.num_speakers, "quick": r.quick,
        "quick_ready": r.quick_transcript is not None,
        "duration_sec": r.duration_sec, "stop_reason": r.stop_reason,
        "job_id": r.job_id, "error_message": r.error_message,
        "created_at": r.created_at.isoformat(),
    }


@router.post("/recordings", status_code=status.HTTP_202_ACCEPTED)
def create_recording(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    url: Annotated[str, Form()],
    num_speakers: Annotated[int | None, Form()] = None,
    quick: Annotated[bool, Form()] = False,
):
    if not URL_RE.search(url):
        raise HTTPException(400, "url must be a Telemost link (telemost[.360].yandex.ru)")
    rec = Recording(
        id=str(uuid.uuid4()), user_id=user.id, url=URL_RE.search(url).group(0),
        num_speakers=num_speakers, quick=quick,
        status="requested", stop_requested=False,
    )
    db.add(rec)
    db.commit()
    return {"recording_id": rec.id, "status": rec.status}


@router.get("/recordings")
def list_recordings(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None, limit: int = 50,
):
    stmt = (select(Recording).where(Recording.user_id == user.id)
            .order_by(Recording.created_at.desc()).limit(limit))
    if status:
        stmt = stmt.where(Recording.status == status)
    return {"recordings": [_view(r) for r in db.scalars(stmt).all()]}


@router.get("/recordings/{rec_id}")
def get_recording(rec_id: str, user: Annotated[User, Depends(current_user)],
                  db: Annotated[Session, Depends(get_db)]):
    rec = db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(404, "recording not found")
    if rec.user_id != user.id:
        raise HTTPException(403, "not your recording")
    return _view(rec)


@router.get("/recordings/{rec_id}/quick")
def get_recording_quick(rec_id: str, user: Annotated[User, Depends(current_user)],
                        db: Annotated[Session, Depends(get_db)]):
    rec = db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(404, "recording not found")
    if rec.user_id != user.id:
        raise HTTPException(403, "not your recording")
    if not rec.quick:
        raise HTTPException(400, "recording was not started with --quick")
    return {
        "recording_id": rec.id, "status": rec.status,
        "ready": rec.quick_transcript is not None,
        "quick_transcript": rec.quick_transcript,
    }


@router.post("/recordings/{rec_id}/stop")
def stop_recording(rec_id: str, user: Annotated[User, Depends(current_user)],
                   db: Annotated[Session, Depends(get_db)]):
    rec = db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(404, "recording not found")
    if rec.user_id != user.id:
        raise HTTPException(403, "not your recording")
    if rec.status not in ACTIVE:
        return {"recording_id": rec.id, "status": rec.status, "stop_requested": False}
    rec.stop_requested = True
    db.commit()
    return {"recording_id": rec.id, "status": rec.status, "stop_requested": True}
