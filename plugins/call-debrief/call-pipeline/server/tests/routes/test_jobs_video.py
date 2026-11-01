import io
import shutil
import subprocess
import uuid
import pytest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="ffmpeg not installed")


def _make_user():
    from app.db import session as s_mod
    from app.db.models import User
    db = s_mod.SessionLocal()
    u = User(id=str(uuid.uuid4()), name="T", bearer_token=str(uuid.uuid4()))
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u


def _video_bytes(tmp_path) -> bytes:
    p = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "color=black:s=64x64:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(p)],
        check=True, capture_output=True)
    return p.read_bytes()


def test_video_upload_extracts_audio(client, tmp_path):
    from app.db import session as s_mod
    from app.db.models import Job
    u = _make_user()
    r = client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {u.bearer_token}"},
        data={"mode": "call", "user_id": u.id},
        files={"file": ("встреча.mp4", io.BytesIO(_video_bytes(tmp_path)), "video/mp4")},
    )
    assert r.status_code == 202
    db = s_mod.SessionLocal()
    job = db.get(Job, r.json()["job_id"])
    assert job.audio_path.endswith(".ogg")          # хранится аудио, не видео
    assert job.original_filename == "встреча.mp4"   # имя — исходное
    db.close()


def test_garbage_video_422(client):
    u = _make_user()
    r = client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {u.bearer_token}"},
        data={"mode": "call", "user_id": u.id},
        files={"file": ("bad.mp4", io.BytesIO(b"garbage"), "video/mp4")},
    )
    assert r.status_code == 422
