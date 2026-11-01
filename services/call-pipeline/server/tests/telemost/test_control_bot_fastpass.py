"""TDD: fast-pass mode=short job + dedupe_key linking (Task 1).

Tests:
1. _sha256 is stable and matches hashlib.sha256 over the same bytes.
2. _submit_short_job posts to /jobs with mode=short, user_id=PIPE_USER,
   dedupe_key="{sha}:short", and includes a file part.

control_bot imports `telegram` and `telegram.ext` which are not installed in
the server venv (the bot runs in a separate environment).  We stub those
packages via sys.modules before importing the module under test.
"""
import hashlib
import sys
import types
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Stub the telegram packages so control_bot can be imported without the real
# python-telegram-bot wheel being installed in the test venv.
# ---------------------------------------------------------------------------
def _stub_telegram():
    """Inject minimal telegram stubs into sys.modules."""
    tg = types.ModuleType("telegram")
    tg_ext = types.ModuleType("telegram.ext")

    # Names control_bot imports from `telegram`
    for name in (
        "BotCommand",
        "BotCommandScopeAllPrivateChats",
        "BotCommandScopeChat",
        "BotCommandScopeDefault",
        "InlineKeyboardButton",
        "InlineKeyboardMarkup",
        "MessageOriginUser",
        "Update",
    ):
        setattr(tg, name, MagicMock(name=f"telegram.{name}"))

    # Names control_bot imports from `telegram.ext`
    for name in (
        "Application",
        "CallbackQueryHandler",
        "CommandHandler",
        "ContextTypes",
        "MessageHandler",
        "filters",
    ):
        setattr(tg_ext, name, MagicMock(name=f"telegram.ext.{name}"))

    sys.modules.setdefault("telegram", tg)
    sys.modules.setdefault("telegram.ext", tg_ext)

    # Also stub any sub-modules that may be lazily imported
    for sub in ("telegram.constants", "telegram.request", "telegram.error"):
        sys.modules.setdefault(sub, types.ModuleType(sub))


_stub_telegram()

# Now safe to import the module under test.
# Remove any stale cached copy first so env-var monkeypatches take effect.
import importlib
import os

import pytest


# ---------------------------------------------------------------------------
# Fixture: set required env vars BEFORE control_bot is imported.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _bot_env(monkeypatch):
    monkeypatch.setenv("TELEMOST_BOT_TOKEN", "999:fake_token")
    monkeypatch.setenv("BOT_USER_ID", "test_pipe_user")
    monkeypatch.setenv("BOT_BEARER_TOKEN", "test_bearer")
    monkeypatch.setenv("BOT_API_URL", "http://127.0.0.1:8001")


# ---------------------------------------------------------------------------
# Lazy import helper — re-imports after env vars are set per test.
# ---------------------------------------------------------------------------
def _import_bot():
    for key in list(sys.modules):
        if "telemost.control_bot" in key or key == "app.telemost.control_bot":
            del sys.modules[key]
    return importlib.import_module("app.telemost.control_bot")


# ---------------------------------------------------------------------------
# Test 1: _sha256 helper
# ---------------------------------------------------------------------------
def test_sha256_stable_and_correct(tmp_path):
    bot = _import_bot()
    content = b"hello telemost fast-pass" * 1000
    f = tmp_path / "audio.ogg"
    f.write_bytes(content)

    result = bot._sha256(str(f))

    expected = hashlib.sha256(content).hexdigest()
    assert result == expected, "sha256 mismatch vs hashlib.sha256"
    # Stability: calling twice gives same value
    assert bot._sha256(str(f)) == result


# ---------------------------------------------------------------------------
# Test 2: _submit_short_job posts correct data + file (including transcript)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_short_job_posts_correct_payload(tmp_path, monkeypatch):
    """_submit_short_job(ogg, sha, transcript) must POST:
    - mode=short
    - dedupe_key={sha}:short
    - transcript=<the text>
    - a file part
    """
    bot = _import_bot()

    ogg = tmp_path / "rec.ogg"
    ogg.write_bytes(b"\x00" * 64)
    sha = "deadbeef" * 8  # fake sha
    transcript_text = "Быстрый Groq-черновик транскрипта."

    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, headers=None, files=None, data=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["files"] = files
            captured["data"] = data
            return _FakeResponse()

    monkeypatch.setattr(bot.httpx, "AsyncClient", _FakeClient)

    # New signature: three positional args — ogg, sha, transcript
    await bot._submit_short_job(str(ogg), sha, transcript_text, bot.PIPE_USER, {})

    assert captured, "_submit_short_job never called httpx.AsyncClient.post"
    assert captured["url"].endswith("/jobs"), f"unexpected URL: {captured['url']}"
    assert captured["data"]["mode"] == "short"
    assert captured["data"]["user_id"] == bot.PIPE_USER
    assert captured["data"]["dedupe_key"] == f"{sha}:short"
    assert captured["data"]["transcript"] == transcript_text, (
        f"transcript not in POST data: {captured['data']}"
    )
    assert "file" in captured["files"], "no file part in POST"
    # origin: this is the bot's own recorder submitting a job, not a forwarded
    # third-party file — the gate must not fall back to "unknown" here.
    assert captured["data"]["origin"] == "telemost"


# ---------------------------------------------------------------------------
# Test 3: _diarize_and_send posts mode=call with origin=telemost
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_diarize_and_send_posts_origin_telemost(tmp_path, monkeypatch):
    """_diarize_and_send (the diarization / mode=call submission path) must
    also mark the job as owner-originated, same as the mode=short fast-pass."""
    bot = _import_bot()

    ogg = tmp_path / "rec.ogg"
    ogg.write_bytes(b"\x00" * 64)

    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"job_id": "job123"}

    class _FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, headers=None, files=None, data=None):
            captured["url"] = url
            captured["data"] = data
            return _FakeResponse()

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            pass

    monkeypatch.setattr(bot.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(bot, "_pipe_identity", lambda cid: (bot.PIPE_USER, {}))
    monkeypatch.setattr(bot, "app", type("A", (), {"bot": _Bot()})())
    monkeypatch.setattr(bot, "_pending_jobs_add", lambda *a, **kw: None)

    async def fake_poll(*a, **kw):
        pass

    monkeypatch.setattr(bot, "_poll_and_send", fake_poll)

    await bot._diarize_and_send(chat_id=1, ogg=str(ogg), n=3, sha="deadbeef" * 8)

    assert captured, "_diarize_and_send never called httpx.AsyncClient.post"
    assert captured["url"].endswith("/jobs"), f"unexpected URL: {captured['url']}"
    assert captured["data"]["mode"] == "call"
    assert captured["data"]["origin"] == "telemost"
