import io
import pytest
from sqlalchemy.orm import Session
from app.db.models import User
from app.db import session as session_mod


@pytest.fixture
def user(client):
    s = Session(session_mod.engine)
    u = User(id="u-test", name="Irina", bearer_token="t-test")
    s.add(u)
    s.commit()
    yield u
    s.close()


def _post(client, *, mode="call", token="t-test", file_bytes=b"RIFFdummy", dedupe=None, filename="x.wav"):
    data = {"mode": mode, "user_id": "u-test"}
    if dedupe:
        data["dedupe_key"] = dedupe
    return client.post(
        "/jobs",
        files={"file": (filename, io.BytesIO(file_bytes), "audio/wav")},
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_post_jobs_unauth(client):
    r = client.post("/jobs", data={"mode": "call", "user_id": "u-test"},
                    files={"file": ("x.wav", io.BytesIO(b"x"), "audio/wav")})
    assert r.status_code == 401


def test_post_jobs_invalid_token(client, user):
    r = _post(client, token="wrong")
    assert r.status_code == 403


def test_post_jobs_creates_uploaded(client, user):
    r = _post(client)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "uploaded"
    assert "job_id" in body


def test_post_jobs_dedupe_returns_existing(client, user):
    a = _post(client, dedupe="abc")
    assert a.status_code == 202
    b = _post(client, dedupe="abc")
    assert b.status_code == 200
    assert a.json()["job_id"] == b.json()["job_id"]


def test_post_jobs_too_large(client, user, monkeypatch):
    # Cap MAX_BYTES at 5 bytes for this test instead of allocating 1 GB
    import app.routes.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "MAX_BYTES", 5)
    r = _post(client, file_bytes=b"x" * 100)
    assert r.status_code == 413


def test_post_jobs_invalid_mode(client, user):
    r = _post(client, mode="weird")
    assert r.status_code == 400


def test_get_job_by_id(client, user):
    r = _post(client)
    jid = r.json()["job_id"]
    g = client.get(f"/jobs/{jid}", headers={"Authorization": "Bearer t-test"})
    assert g.status_code == 200
    assert g.json()["status"] == "uploaded"


def test_get_job_other_user_forbidden(client, user):
    s = Session(session_mod.engine)
    s.add(User(id="u-other", name="O", bearer_token="t-other"))
    s.commit()
    s.close()
    r = _post(client)
    jid = r.json()["job_id"]
    g = client.get(f"/jobs/{jid}", headers={"Authorization": "Bearer t-other"})
    assert g.status_code == 403


def test_get_transcript_404_when_not_ready(client, user):
    r = _post(client)
    jid = r.json()["job_id"]
    t = client.get(f"/jobs/{jid}/transcript", headers={"Authorization": "Bearer t-test"})
    assert t.status_code == 404


def test_get_draft_404_until_drafted(client, user):
    r = _post(client)
    jid = r.json()["job_id"]
    g = client.get(f"/jobs/{jid}/draft", headers={"Authorization": "Bearer t-test"})
    assert g.status_code == 404


def test_list_jobs(client, user):
    _post(client)
    _post(client)
    r = client.get("/jobs", headers={"Authorization": "Bearer t-test"})
    assert r.status_code == 200
    assert len(r.json()["jobs"]) == 2


def test_delete_job(client, user):
    r = _post(client)
    jid = r.json()["job_id"]
    d = client.delete(f"/jobs/{jid}", headers={"Authorization": "Bearer t-test"})
    assert d.status_code == 200
    g = client.get(f"/jobs/{jid}", headers={"Authorization": "Bearer t-test"})
    assert g.status_code == 404


def test_get_job_includes_dedupe_key(client, user):
    """Test that get_job response includes dedupe_key."""
    r = _post(client, dedupe="test-dedup-key-123")
    jid = r.json()["job_id"]
    g = client.get(f"/jobs/{jid}", headers={"Authorization": "Bearer t-test"})
    assert g.status_code == 200
    body = g.json()
    assert "dedupe_key" in body
    assert body["dedupe_key"] == "test-dedup-key-123"


def test_list_jobs_includes_dedupe_key(client, user):
    """Test that list_jobs response includes dedupe_key for each job."""
    r1 = _post(client, dedupe="dedup-1")
    r2 = _post(client, dedupe="dedup-2")
    jid1 = r1.json()["job_id"]
    jid2 = r2.json()["job_id"]

    l = client.get("/jobs", headers={"Authorization": "Bearer t-test"})
    assert l.status_code == 200
    jobs = l.json()["jobs"]
    assert len(jobs) == 2

    # Find jobs by ID and check dedupe_key
    job_map = {j["job_id"]: j for j in jobs}
    assert "dedupe_key" in job_map[jid1]
    assert job_map[jid1]["dedupe_key"] == "dedup-1"
    assert "dedupe_key" in job_map[jid2]
    assert job_map[jid2]["dedupe_key"] == "dedup-2"


def _post_with_transcript(client, *, transcript: str, mode="short", token="t-test"):
    """Helper: POST /jobs with a transcript field."""
    import io
    data = {"mode": mode, "user_id": "u-test", "transcript": transcript}
    return client.post(
        "/jobs",
        files={"file": ("rec.ogg", io.BytesIO(b"OggS" * 16), "audio/ogg")},
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_create_job_with_transcript(client, user):
    """POSTing a transcript field skips whisper: job is immediately 'transcribed'
    and GET /jobs/{id}/transcript returns the pre-supplied text."""
    from app.lib import storage

    text = "Это быстрый транскрипт от Groq."
    r = _post_with_transcript(client, transcript=text)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "transcribed", (
        f"expected 'transcribed', got {body['status']!r}"
    )
    jid = body["job_id"]

    # Transcript file must exist at storage.transcript_path(user_id, job_id)
    tp = storage.transcript_path("u-test", jid)
    assert tp.exists(), f"transcript file not found at {tp}"

    import json
    payload = json.loads(tp.read_text(encoding="utf-8"))
    assert payload["text"] == text

    # GET /jobs/{id}/transcript must return the payload
    tr = client.get(f"/jobs/{jid}/transcript", headers={"Authorization": "Bearer t-test"})
    assert tr.status_code == 200
    assert tr.json()["text"] == text


def test_create_job_without_transcript(client, user):
    """Without a transcript field the job stays 'uploaded' (unchanged behaviour)."""
    r = _post(client)
    assert r.status_code == 202
    assert r.json()["status"] == "uploaded"


def test_post_jobs_stores_and_returns_origin(client, user):
    r = client.post(
        "/jobs",
        files={"file": ("x.wav", io.BytesIO(b"RIFFdummy"), "audio/wav")},
        data={"mode": "call", "user_id": "u-test", "origin": "zoom"},
        headers={"Authorization": "Bearer t-test"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    detail = client.get(f"/jobs/{job_id}", headers={"Authorization": "Bearer t-test"})
    assert detail.json()["origin"] == "zoom"
    listing = client.get("/jobs", headers={"Authorization": "Bearer t-test"})
    assert listing.json()["jobs"][0]["origin"] == "zoom"


def test_post_jobs_origin_defaults_to_unknown(client, user):
    r = _post(client)
    job_id = r.json()["job_id"]
    detail = client.get(f"/jobs/{job_id}", headers={"Authorization": "Bearer t-test"})
    assert detail.json()["origin"] == "unknown"


def test_post_jobs_rejects_unknown_origin_value(client, user):
    r = client.post(
        "/jobs",
        files={"file": ("x.wav", io.BytesIO(b"RIFFdummy"), "audio/wav")},
        data={"mode": "call", "user_id": "u-test", "origin": "malicious"},
        headers={"Authorization": "Bearer t-test"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    detail = client.get(f"/jobs/{job_id}", headers={"Authorization": "Bearer t-test"})
    assert detail.json()["origin"] == "unknown"
    listing = client.get("/jobs", headers={"Authorization": "Bearer t-test"})
    assert listing.json()["jobs"][0]["origin"] == "unknown"


def _recorded_at(client, job_id):
    r = client.get(f"/jobs/{job_id}", headers={"Authorization": "Bearer t-test"})
    return r.json()["recorded_at"]


def test_recorded_at_from_filename(client, user):
    r = _post(client, filename="telemost_20260907_072429_2774218.ogg")
    assert _recorded_at(client, r.json()["job_id"]) == "2026-09-07"


def test_recorded_at_falls_back_to_client_date(client, user):
    """У zoom-выгрузок и скачанных записей даты в имени нет — берём запасную от клиента."""
    r = client.post(
        "/jobs",
        files={"file": ("video1855516632.opus", io.BytesIO(b"x"), "audio/wav")},
        data={"mode": "call", "user_id": "u-test", "fallback_recorded_at": "2026-09-07"},
        headers={"Authorization": "Bearer t-test"},
    )
    assert _recorded_at(client, r.json()["job_id"]) == "2026-09-07"


def test_filename_date_wins_over_fallback(client, user):
    """Дата в имени — про саму запись, mtime — про файл на диске; имя авторитетнее."""
    r = client.post(
        "/jobs",
        files={"file": ("Запись 2026-08-01 14-30.opus", io.BytesIO(b"x"), "audio/wav")},
        data={"mode": "call", "user_id": "u-test", "fallback_recorded_at": "2026-09-07"},
        headers={"Authorization": "Bearer t-test"},
    )
    assert _recorded_at(client, r.json()["job_id"]) == "2026-08-01"


def test_garbage_fallback_ignored(client, user):
    r = client.post(
        "/jobs",
        files={"file": ("video1855516632.opus", io.BytesIO(b"x"), "audio/wav")},
        data={"mode": "call", "user_id": "u-test", "fallback_recorded_at": "не дата"},
        headers={"Authorization": "Bearer t-test"},
    )
    assert _recorded_at(client, r.json()["job_id"]) is None
