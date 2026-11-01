import os
import shutil
from datetime import datetime
from sqlalchemy.orm import Session
from ..db import session as session_mod
from ..db.models import DiskState

WARN_PCT = 85.0
WARN_FREE_GB = 5.0


def _root() -> str:
    root = os.environ.get("PIPELINE_STORAGE_ROOT", "/var/lib/call-pipeline")
    return root if os.path.exists(root) else "/"


def refresh() -> None:
    u = shutil.disk_usage(_root())
    used_pct = u.used / u.total * 100
    free_gb = u.free / 1024**3
    warning = used_pct > WARN_PCT or free_gb < WARN_FREE_GB

    s: Session = session_mod.SessionLocal()
    try:
        state = s.get(DiskState, 1)
        if state is None:
            state = DiskState(id=1, usage_pct=used_pct, free_gb=free_gb, warning=warning)
            s.add(state)
        else:
            state.usage_pct = used_pct
            state.free_gb = free_gb
            state.warning = warning
            state.updated_at = datetime.utcnow()
        s.commit()
    finally:
        s.close()


if __name__ == "__main__":
    refresh()
