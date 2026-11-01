"""Новые колонки для веб-сервиса присутствуют и имеют правильные дефолты."""
import uuid


def _fresh_session():
    from app.db import session as s_mod
    return s_mod.SessionLocal()


def test_user_web_columns_defaults(client):
    from app.db.models import User
    db = _fresh_session()
    u = User(id=str(uuid.uuid4()), name="T", bearer_token=str(uuid.uuid4()))
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.email is None
    assert u.password_hash is None
    assert u.is_admin is False
    assert u.is_active is True
    db.close()


def test_job_original_filename_column(client):
    from app.db.models import Job, User
    db = _fresh_session()
    u = User(id=str(uuid.uuid4()), name="T", bearer_token=str(uuid.uuid4()))
    db.add(u)
    db.commit()
    j = Job(id=str(uuid.uuid4()), user_id=u.id, status="uploaded", mode="short",
            diarize=False, audio_path="/tmp/x.ogg", original_filename="звонок.mp4")
    db.add(j)
    db.commit()
    db.refresh(j)
    assert j.original_filename == "звонок.mp4"
    db.close()
