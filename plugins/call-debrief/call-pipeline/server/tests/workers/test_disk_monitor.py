from unittest.mock import patch
from sqlalchemy.orm import Session
from app.db.models import DiskState
from app.db import session as session_mod
from app.workers.disk_monitor import refresh


def test_refresh_writes_warning_when_low(client):
    fake = type("U", (), {"total": 100 * 1024**3, "used": 90 * 1024**3, "free": 10 * 1024**3})
    with patch("app.workers.disk_monitor.shutil.disk_usage", return_value=fake):
        refresh()
    s = Session(session_mod.engine)
    state = s.get(DiskState, 1)
    assert state.warning is True
    assert 8 < state.free_gb < 12
    s.close()


def test_refresh_no_warning_when_ok(client):
    fake = type("U", (), {"total": 100 * 1024**3, "used": 50 * 1024**3, "free": 50 * 1024**3})
    with patch("app.workers.disk_monitor.shutil.disk_usage", return_value=fake):
        refresh()
    s = Session(session_mod.engine)
    state = s.get(DiskState, 1)
    assert state.warning is False
    s.close()


def test_refresh_warns_on_low_free_gb(client):
    # 80% used (under 85%) but free < 5 GB → still warns
    fake = type("U", (), {"total": 10 * 1024**3, "used": 7 * 1024**3, "free": 3 * 1024**3})
    with patch("app.workers.disk_monitor.shutil.disk_usage", return_value=fake):
        refresh()
    s = Session(session_mod.engine)
    state = s.get(DiskState, 1)
    assert state.warning is True
    s.close()
