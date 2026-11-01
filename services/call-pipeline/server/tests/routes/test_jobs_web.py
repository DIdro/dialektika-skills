import io
import uuid


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


def _upload(client, u, filename="созвон 2026-07-18.ogg"):
    return client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {u.bearer_token}"},
        data={"mode": "short", "user_id": u.id},
        files={"file": (filename, io.BytesIO(b"fake-audio-bytes"), "audio/ogg")},
    )


def test_original_filename_saved_and_listed(client):
    u = _make_user()
    r = _upload(client, u)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    lst = client.get("/jobs", headers={"Authorization": f"Bearer {u.bearer_token}"})
    row = next(j for j in lst.json()["jobs"] if j["job_id"] == job_id)
    assert row["original_filename"] == "созвон 2026-07-18.ogg"
    assert "error_message" in row

    one = client.get(f"/jobs/{job_id}",
                     headers={"Authorization": f"Bearer {u.bearer_token}"})
    assert one.json()["original_filename"] == "созвон 2026-07-18.ogg"


def test_download_audio(client):
    u = _make_user()
    job_id = _upload(client, u).json()["job_id"]
    r = client.get(f"/jobs/{job_id}/audio",
                   headers={"Authorization": f"Bearer {u.bearer_token}"})
    assert r.status_code == 200
    assert r.content == b"fake-audio-bytes"


def test_download_audio_foreign_job_403(client):
    u1, u2 = _make_user(), _make_user()
    job_id = _upload(client, u1).json()["job_id"]
    r = client.get(f"/jobs/{job_id}/audio",
                   headers={"Authorization": f"Bearer {u2.bearer_token}"})
    assert r.status_code == 403
