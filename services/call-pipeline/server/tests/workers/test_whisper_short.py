import json
import pytest
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.db.models import User, Job
from app.db import session as session_mod
from app.workers.whisper_short import process_one


@pytest.fixture
def setup(client, tmp_path):
    s = Session(session_mod.engine)
    s.add(User(id="u", name="A", bearer_token="t")); s.commit()
    audio = tmp_path / "x.wav"; audio.write_bytes(b"RIFFdata")
    s.add(Job(id="j", user_id="u", status="uploaded", mode="short",
              diarize=False, audio_path=str(audio)))
    s.commit()
    s.close()


def test_process_one_writes_transcript(setup):
    with patch("app.workers.whisper_short.transcribe",
               return_value={"language": "ru", "duration_sec": 5.0,
                             "segments": [{"start": 0, "end": 1, "text": "hi"}]}):
        ok = process_one()
    assert ok
    s = Session(session_mod.engine)
    j = s.get(Job, "j")
    assert j.status == "transcribed"
    assert j.transcript_path
    data = json.loads(open(j.transcript_path, encoding="utf-8").read())
    assert data["text"] == "hi"
    assert data["diarized"] is False
    s.close()


def test_process_one_returns_false_when_empty(client):
    assert process_one() is False
