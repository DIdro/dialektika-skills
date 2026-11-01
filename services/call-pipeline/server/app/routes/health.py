from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db.models import DiskState
from ..deps import get_db

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    state = db.scalar(select(DiskState).where(DiskState.id == 1))
    return {
        "ok": True,
        "disk_warning": bool(state.warning) if state else False,
        "disk_free_gb": state.free_gb if state else None,
    }
