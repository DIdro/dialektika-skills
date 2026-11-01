import json
import pytest
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.db.models import User, Job
from app.db import session as session_mod
from app.workers.whisper_call import process_one


@pytest.fixture
def setup(client, tmp_path):
    s = Session(session_mod.engine)
    s.add(User(id="u", name="A", bearer_token="t")); s.commit()
    audio = tmp_path / "x.wav"; audio.write_bytes(b"RIFFdata")
    s.add(Job(id="j", user_id="u", status="uploaded", mode="call",
              diarize=True, audio_path=str(audio)))
    s.commit()
    s.close()


def test_process_one_diarizes(setup):
    fake_segs = [{"start": 0, "end": 1, "text": "hi"}]
    fake_spans = [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]
    with patch("app.workers.whisper_call.transcribe",
               return_value={"language": "ru", "duration_sec": 5.0,
                             "segments": fake_segs}), \
         patch("app.workers.whisper_call.to_whisper_wav") as fake_norm, \
         patch("app.workers.whisper_call.diarize", return_value=fake_spans):
        # to_whisper_wav writes the destination wav; in tests we just touch it.
        fake_norm.side_effect = lambda src, dst: open(dst, "wb").close()
        ok = process_one()
    assert ok
    # to_whisper_wav must have been called between whisper and diarize.
    assert fake_norm.called
    s = Session(session_mod.engine)
    j = s.get(Job, "j")
    assert j.status == "transcribed"
    assert j.transcript_path
    data = json.loads(open(j.transcript_path, encoding="utf-8").read())
    assert data["diarized"] is True
    assert data["segments"][0]["speaker"] == "SPEAKER_00"
    s.close()


def test_process_one_no_diarize_skips_wav_normalize(client, tmp_path):
    """When job.diarize is False, we should not invoke to_whisper_wav at all."""
    s = Session(session_mod.engine)
    s.add(User(id="u2", name="B", bearer_token="t2"))
    s.commit()
    audio = tmp_path / "y.opus"; audio.write_bytes(b"OggS")
    s.add(Job(id="j2", user_id="u2", status="uploaded", mode="call",
              diarize=False, audio_path=str(audio)))
    s.commit()
    s.close()

    fake_segs = [{"start": 0, "end": 1, "text": "hi"}]
    with patch("app.workers.whisper_call.transcribe",
               return_value={"language": "ru", "duration_sec": 5.0,
                             "segments": fake_segs}), \
         patch("app.workers.whisper_call.to_whisper_wav") as fake_norm, \
         patch("app.workers.whisper_call.diarize") as fake_diarize:
        ok = process_one()
    assert ok
    assert not fake_norm.called
    assert not fake_diarize.called


def test_diarize_failure_keeps_transcript(setup):
    """Падение pyannote не должно уносить уже посчитанный whisper."""
    fake_segs = [{"start": 0, "end": 1, "text": "hi"}]
    with patch("app.workers.whisper_call.transcribe",
               return_value={"language": "ru", "duration_sec": 5.0,
                             "segments": fake_segs}), \
         patch("app.workers.whisper_call.to_whisper_wav") as fake_norm, \
         patch("app.workers.whisper_call.diarize",
               side_effect=RuntimeError("HF_TOKEN required for pyannote diarization")):
        fake_norm.side_effect = lambda src, dst: open(dst, "wb").close()
        ok = process_one()
    assert ok
    s = Session(session_mod.engine)
    j = s.get(Job, "j")
    assert j.status == "transcribed"
    assert j.transcript_path
    data = json.loads(open(j.transcript_path, encoding="utf-8").read())
    assert data["diarized"] is False
    assert data["segments"][0]["speaker"] == "UNKNOWN"
    assert data["text"] == "hi"
    s.close()
