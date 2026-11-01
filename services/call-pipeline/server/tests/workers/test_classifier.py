import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.orm import Session
from app.db.models import User, Job
from app.db import session as session_mod
from app.workers.classifier import process_one


@pytest.fixture
def setup_transcribed(client, tmp_path):
    s = Session(session_mod.engine)
    s.add(User(
        id="u", name="Ирина", bearer_token="t",
        context_summary="Ирина, руководитель студии",
    ))
    s.commit()
    audio = tmp_path / "x.opus"; audio.write_bytes(b"x")
    tpath = tmp_path / "t.json"
    tpath.write_text(json.dumps({
        "job_id": "j", "language": "ru", "duration_sec": 5.0,
        "diarized": True,
        "text": "hello",
        "segments": [{"start": 0, "end": 1, "text": "hello",
                      "speaker": "S0"}],
    }), encoding="utf-8")
    s.add(Job(id="j", user_id="u", status="transcribed", mode="call",
              diarize=True, audio_path=str(audio),
              transcript_path=str(tpath)))
    s.commit()
    s.close()


def _tool_use_block(input_dict):
    """Mock a content block representing Anthropic's submit_draft tool_use."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_draft"
    block.input = input_dict
    return block


@pytest.mark.asyncio
async def test_process_one_writes_draft(setup_transcribed):
    fake_draft = {
        "summary": "sum",
        "primary_project": {"key": "bot", "confidence": 0.9, "rationale": "r"},
        "alternative_projects": [],
        "uncertainty": None,
        "actions": [],
        "secondary_destinations": [],
    }
    fake_anthropic = MagicMock()
    fake_anthropic.messages.create = AsyncMock(return_value=MagicMock(
        content=[_tool_use_block(fake_draft)]
    ))
    with patch("app.workers.classifier._client", return_value=fake_anthropic), \
         patch("app.workers.classifier.FileProvider") as MP:
        MP.return_value.is_available.return_value = True
        MP.return_value.fetch_context = AsyncMock(return_value={
            "projects": [{"key": "bot", "name": "Bot", "description": "x"}]
        })
        ok = await process_one()
    assert ok
    s = Session(session_mod.engine)
    j = s.get(Job, "j")
    assert j.status == "drafted"
    assert j.draft_path
    draft = json.loads(open(j.draft_path, encoding="utf-8").read())
    assert draft["primary_project"]["key"] == "bot"
    s.close()


@pytest.mark.asyncio
async def test_process_one_returns_false_when_empty(client):
    assert await process_one() is False


@pytest.mark.asyncio
async def test_classifier_uses_tool_use(setup_transcribed):
    """Verify the worker is wired to read tool_use input (not raw text)."""
    fake_draft = {
        "summary": "tool-call response",
        "primary_project": None,
        "alternative_projects": [],
        "uncertainty": None,
        "actions": [],
        "secondary_destinations": [],
    }
    fake_anthropic = MagicMock()
    fake_anthropic.messages.create = AsyncMock(return_value=MagicMock(
        content=[_tool_use_block(fake_draft)]
    ))
    with patch("app.workers.classifier._client", return_value=fake_anthropic), \
         patch("app.workers.classifier.FileProvider") as MP:
        MP.return_value.is_available.return_value = False
        MP.return_value.fetch_context = AsyncMock(return_value={"projects": []})
        ok = await process_one()
    assert ok
    # Verify the worker passed `tools` and `tool_choice` to messages.create
    kwargs = fake_anthropic.messages.create.call_args.kwargs
    assert "tools" in kwargs
    assert kwargs["tool_choice"]["name"] == "submit_draft"
    s = Session(session_mod.engine)
    j = s.get(Job, "j")
    assert j.status == "drafted"
    s.close()
