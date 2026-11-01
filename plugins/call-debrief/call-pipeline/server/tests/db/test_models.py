import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.db.models import Base, User, Job

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s

def test_create_user(db):
    u = User(id="u-1", name="Irina", bearer_token="t-1")
    db.add(u); db.commit()
    got = db.scalar(select(User).where(User.id == "u-1"))
    assert got.name == "Irina"

def test_create_job_with_fk(db):
    db.add(User(id="u-1", name="Irina", bearer_token="t-1")); db.commit()
    db.add(Job(id="j-1", user_id="u-1", status="uploaded", mode="call",
               diarize=True, audio_path="/x/a.opus"))
    db.commit()
    j = db.scalar(select(Job).where(Job.id == "j-1"))
    assert j.user_id == "u-1"
    assert j.status == "uploaded"
