"""Short (speaker-less) job must NOT reach the pipeline when the participant
count is answered — the diarised call job is the one the downstream consumer should see.
It is submitted only as a fallback after SHORT_FALLBACK_SEC without a count."""
import sys
import pytest
from .test_control_bot_fastpass import _import_bot, _bot_env  # noqa: F401


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


def _capture_submit(bot, monkeypatch):
    calls = []

    async def fake_submit(ogg, sha, text, uid, auth):
        calls.append({"ogg": ogg, "sha": sha, "text": text})

    monkeypatch.setattr(bot, "_submit_short_job", fake_submit)
    monkeypatch.setattr(bot, "_pipe_identity", lambda cid: ("u", {}))
    monkeypatch.setattr(bot, "SHORT_FALLBACK_SEC", 0)
    monkeypatch.setattr(bot, "app", type("A", (), {"bot": _Bot()})())
    return calls


@pytest.mark.asyncio
async def test_fallback_skipped_when_count_answered(monkeypatch):
    bot = _import_bot()
    calls = _capture_submit(bot, monkeypatch)
    bot.PENDING[1] = {"ogg": "x.ogg", "sha": "abc", "quick_text": "hi"}
    bot.PENDING.pop(1)  # user answered the count → PENDING cleared
    await bot._short_fallback(1, "x.ogg", "abc")
    assert calls == []


@pytest.mark.asyncio
async def test_fallback_submits_when_no_count(monkeypatch):
    bot = _import_bot()
    calls = _capture_submit(bot, monkeypatch)
    bot.PENDING[1] = {"ogg": "x.ogg", "sha": "abc", "quick_text": "hi"}
    await bot._short_fallback(1, "x.ogg", "abc")
    assert len(calls) == 1 and calls[0]["text"] == "hi" and calls[0]["sha"] == "abc"
    assert bot.PENDING[1].get("short_submitted") is True
    assert any("без спикеров" in m for m in bot.app.bot.sent)


@pytest.mark.asyncio
async def test_quick_transcript_does_not_submit_short_directly(monkeypatch):
    bot = _import_bot()
    calls = _capture_submit(bot, monkeypatch)

    async def fake_groq(ogg):
        return "groq text"

    monkeypatch.setattr(bot, "groq_quick_transcript", fake_groq)
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro) or coro.close())
    bot.PENDING[7] = {"ogg": "y.ogg", "sha": "s7"}
    await bot._send_quick_transcript(7, "y.ogg", "s7")
    assert calls == []                      # nothing submitted synchronously
    assert bot.PENDING[7]["quick_text"] == "groq text"
    assert len(created) == 1                # fallback scheduled
