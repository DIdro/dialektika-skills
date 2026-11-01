import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.models import Base, User, Job
from app.db import queries as q


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(id="u", name="Irina", bearer_token="tok"))
        s.commit()
        yield s


def test_find_user_by_token(session):
    u = q.find_user_by_token(session, "tok")
    assert u and u.id == "u"
    assert q.find_user_by_token(session, "nope") is None


def test_find_job_by_dedupe(session):
    session.add(Job(id="j1", user_id="u", status="uploaded", mode="call",
                    diarize=True, audio_path="/a.opus", dedupe_key="abc"))
    session.commit()
    assert q.find_job_by_dedupe(session, "u", "abc").id == "j1"
    assert q.find_job_by_dedupe(session, "u", "xyz") is None


def test_claim_next_job_picks_uploaded_for_short_mode(session):
    session.add_all([
        Job(id="j-c", user_id="u", status="uploaded", mode="call", diarize=True, audio_path="/c"),
        Job(id="j-s", user_id="u", status="uploaded", mode="short", diarize=False, audio_path="/s"),
    ])
    session.commit()
    j = q.claim_next_job(session, modes=["short"], from_status="uploaded", to_status="processing_whisper")
    assert j and j.id == "j-s"
    assert j.status == "processing_whisper"


def test_claim_next_job_returns_none_when_empty(session):
    assert q.claim_next_job(session, modes=["short"], from_status="uploaded", to_status="processing_whisper") is None


# ── восстановление джоб, брошенных убитым воркером ──────────────────────────

def _stranded(session, job_id, *, mode="call", status="processing_whisper", retries=0):
    session.add(Job(id=job_id, user_id="u", status=status, mode=mode,
                    diarize=True, audio_path="/a.opus", retry_count=retries))
    session.commit()


def test_requeue_stranded_returns_job_to_queue(session):
    """SIGTERM посреди расшифровки не даёт отработать ни успеху, ни except —
    строка остаётся в processing_whisper, и claim_next_job мимо неё проходит."""
    _stranded(session, "j1")
    assert q.requeue_stranded(session, modes=["call"], from_status="processing_whisper",
                              to_status="uploaded", terminal_status="failed_whisper",
                              stage="whisper") == ["j1"]
    assert session.get(Job, "j1").status == "uploaded"
    assert session.get(Job, "j1").retry_count == 1


def test_requeue_stranded_ignores_other_modes(session):
    """У каждого режима свой процесс; чужую работу перехватывать нельзя."""
    _stranded(session, "j-short", mode="short")
    assert q.requeue_stranded(session, modes=["call"], from_status="processing_whisper",
                              to_status="uploaded", terminal_status="failed_whisper",
                              stage="whisper") == []
    assert session.get(Job, "j-short").status == "processing_whisper"


def test_requeue_stranded_ignores_other_statuses(session):
    _stranded(session, "j-ok", status="drafted")
    assert q.requeue_stranded(session, modes=["call"], from_status="processing_whisper",
                              to_status="uploaded", terminal_status="failed_whisper",
                              stage="whisper") == []
    assert session.get(Job, "j-ok").status == "drafted"


def test_requeue_stranded_gives_up_after_max_retries(session):
    """Джоба, которая роняет воркер сама (OOM на длинном звонке), не должна
    перезапускать его бесконечно."""
    _stranded(session, "j-bad", retries=3)
    assert q.requeue_stranded(session, modes=["call"], from_status="processing_whisper",
                              to_status="uploaded", terminal_status="failed_whisper",
                              stage="whisper", max_retries=3) == []
    j = session.get(Job, "j-bad")
    assert j.status == "failed_whisper"
    assert "воркер" in (j.error_message or "")


def test_requeue_stranded_handles_classifier_stage(session):
    _stranded(session, "j2", status="processing_classifier")
    assert q.requeue_stranded(session, modes=["call", "short"],
                              from_status="processing_classifier",
                              to_status="transcribed",
                              terminal_status="failed_classifier",
                              stage="classifier") == ["j2"]
    assert session.get(Job, "j2").status == "transcribed"
