#!/usr/bin/env python3
"""Telemost control bot.

A whitelisted user sends a Telemost link (or /record <link>). The bot launches
the local recorder, which auto-stops when the conference ends (or on /stop).
Then the bot asks the launcher how many participants there were, runs that
recording through call-pipeline with num_speakers=N (diarization), and sends
the speaker-separated transcript back to whoever launched it.

Access is per-user. Admins (TELEMOST_ADMIN_IDS) manage the whitelist at runtime
without touching numeric ids: forward a person's message to add them, or
approve an inline request when an unknown user writes to the bot. No groups.

Runs on the call-pipeline host; jobs go to the API on 127.0.0.1, reusing the
voice bot's BOT_API_URL / BOT_USER_ID / BOT_BEARER_TOKEN.

Env (from /etc/call-pipeline/env):
  TELEMOST_BOT_TOKEN  — BotFather token
  TELEMOST_ADMIN_IDS  — comma-separated admin user ids (manage whitelist)
  TELEMOST_WHITELIST  — seed for the whitelist on first run only
  BOT_API_URL / BOT_USER_ID / BOT_BEARER_TOKEN — pipeline API client
"""
import asyncio
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import httpx
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageOriginUser,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .quick_transcript import groq_quick_transcript
from .title import caption_for, file_header, slugify, summarize_title
from .whitelist import Whitelist
from ..lib import storage

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO,
)
_log = logging.getLogger("telemost_control")

TOKEN     = os.environ["TELEMOST_BOT_TOKEN"]
PIPE_BASE = os.environ.get("BOT_API_URL", "http://127.0.0.1:8001").rstrip("/")
PIPE_USER = os.environ["BOT_USER_ID"]
PIPE_AUTH = {"Authorization": f"Bearer {os.environ['BOT_BEARER_TOKEN']}"}

# Per-launcher routing: map a Telegram user id (the private-chat launcher) to
# their own pipeline account, so a recording's jobs (short fast-pass + diarize)
# are submitted UNDER THEM — the draft then lands in THEIR
# account, not the shared bot account. Unmapped launchers fall back to the
# bot account. The file is NOT in the repo (it holds bearers):
#   {"<telegram_id>": {"user_id": "...", "bearer": "..."}}
USER_MAP_FILE = os.environ.get(
    "TELEMOST_USER_MAP_FILE", "/var/lib/call-pipeline/telemost_user_map.json")


def _user_map() -> dict:
    try:
        return json.loads(Path(USER_MAP_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pipe_identity(chat_id: int) -> tuple[str, dict]:
    """Return (pipeline user_id, auth headers) for whoever launched the recording.

    Falls back to the shared bot account for unmapped Telegram ids.
    """
    ent = _user_map().get(str(chat_id))
    if ent and ent.get("user_id") and ent.get("bearer"):
        return ent["user_id"], {"Authorization": f"Bearer {ent['bearer']}"}
    return PIPE_USER, PIPE_AUTH


_DEFAULT_ADMINS = ""


def _parse_ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


# ── fast-pass helpers ────────────────────────────────────────────────────────

def _sha256(path: str) -> str:
    """Stream-hash a file in 1 MB chunks; returns hex digest."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _submit_short_job(ogg: str, sha: str, transcript: str,
                            user_id: str, auth: dict) -> None:
    """Submit a fast mode=short job (worker-shorts, no diarisation).

    Carries the pre-computed Groq transcript so the downstream consumer can build the task
    list immediately without whisper re-transcription.
    Linked to the diarised job by audio sha via dedupe_key ({sha}:short vs
    {sha}:call).  Best-effort — never raises; logs warnings on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            with open(ogg, "rb") as f:
                files = {"file": (os.path.basename(ogg), f, "application/octet-stream")}
                data = {
                    "mode": "short",
                    "user_id": user_id,
                    "dedupe_key": f"{sha}:short",
                    "transcript": transcript,
                    "origin": "telemost",
                }
                r = await c.post(f"{PIPE_BASE}/jobs", headers=auth, files=files, data=data)
            r.raise_for_status()
    except Exception as e:
        _log.warning("short fast-pass job submit failed: %s", e)


ADMIN_IDS = _parse_ids(os.environ.get("TELEMOST_ADMIN_IDS", _DEFAULT_ADMINS))
WL = Whitelist(seed_ids=_parse_ids(os.environ.get("TELEMOST_WHITELIST", "")))

MAX_RECORD_SEC = 4 * 3600
# How long to wait for the participant count before submitting the
# speaker-less Groq transcript as a mode=short job (fallback so the call
# still reaches the classifier). Answered in time → only the diarised job is sent.
SHORT_FALLBACK_SEC = int(os.environ.get("TELEMOST_SHORT_FALLBACK_SEC", str(60 * 60)))
# Matches both consumer Telemost (telemost.yandex.ru) and Yandex 360 / corporate
# Telemost (telemost.360.yandex.ru, and any telemost.<sub>.yandex.ru variant).
URL_RE = re.compile(r"https?://telemost\.(?:[a-z0-9-]+\.)?yandex\.ru/\S+", re.I)

SESSIONS: dict[int, dict] = {}     # chat_id -> {proc, url, log, started}
PENDING:  dict[int, dict] = {}     # chat_id -> {ogg, dur}  (awaiting participant count)
REQUESTS: dict[int, dict] = {}     # user_id -> {name, username}  (pending access requests)
_configured_admins: set[int] = set()

# Diarization jobs already submitted to the pipeline but not yet delivered are
# persisted here so a bot restart (deploy, crash, reboot) re-attaches their
# pollers on startup instead of silently dropping the transcript. Maps
# job_id -> {chat_id, n}. The recording phase itself can't survive a restart
# (the recorder is a child in the bot's systemd cgroup) — this covers the long,
# recoverable post-recording wait, which is where a restart actually hurts.
PENDING_JOBS_FILE = os.environ.get(
    "TELEMOST_PENDING_FILE", "/var/lib/call-pipeline/telemost_pending.json")


def _pending_jobs() -> dict:
    try:
        return json.loads(Path(PENDING_JOBS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pending_jobs_save(d: dict) -> None:
    try:
        p = Path(PENDING_JOBS_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        _log.warning("could not persist pending jobs: %s", e)


def _pending_jobs_add(job_id: str, chat_id: int, n: int) -> None:
    d = _pending_jobs()
    d[job_id] = {"chat_id": chat_id, "n": n}
    _pending_jobs_save(d)


def _pending_jobs_del(job_id: str) -> None:
    d = _pending_jobs()
    if d.pop(job_id, None) is not None:
        _pending_jobs_save(d)

PUBLIC_CMDS = [
    BotCommand("record", "Записать конференцию: /record <ссылка>"),
    BotCommand("stop", "Остановить текущую запись"),
]
ADMIN_CMDS = PUBLIC_CMDS + [
    BotCommand("allow", "Выдать доступ (перешли/ответь)"),
    BotCommand("deny", "Забрать доступ"),
    BotCommand("list", "Кто имеет доступ"),
]


def _uid(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def _is_admin(update: Update) -> bool:
    return _uid(update) in ADMIN_IDS


def _is_allowed(update: Update) -> bool:
    uid = _uid(update)
    return uid is not None and (uid in WL or uid in ADMIN_IDS)


def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


def _fmt(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _label(name: str | None, username: str | None, fallback: int) -> str:
    s = name or str(fallback)
    if username:
        s += f" (@{username})"
    return s


async def _ensure_admin_menu(bot, uid: int | None) -> None:
    if uid is None or uid not in ADMIN_IDS or uid in _configured_admins:
        return
    try:
        await bot.set_my_commands(ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=uid))
        _configured_admins.add(uid)
    except Exception as e:
        _log.info("admin menu for %s not set yet: %s", uid, e)


def _target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolve (id, user) to act on: replied-to author, forwarded sender, or a
    numeric arg. Returns (None, None) if unresolvable (e.g. hidden forward)."""
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, u
    fo = getattr(msg, "forward_origin", None)
    if isinstance(fo, MessageOriginUser):
        return fo.sender_user.id, fo.sender_user
    if context.args and context.args[0].lstrip("-").isdigit():
        return int(context.args[0]), None
    return None, None


# ── recording lifecycle ────────────────────────────────────────────

async def _start_recording(update: Update, url: str) -> None:
    chat_id = update.effective_chat.id
    if SESSIONS:
        await update.effective_message.reply_text(
            "⏳ Сейчас уже идёт запись (одна за раз). Дождись окончания или /stop.")
        return
    log_path = f"/tmp/telemost_rec_{chat_id}_{int(time.time())}.log"
    log_fh = open(log_path, "wb")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.telemost.recorder", url, str(MAX_RECORD_SEC),
        stdout=log_fh, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    SESSIONS[chat_id] = {"proc": proc, "url": url, "log": log_path, "started": time.time()}
    await update.effective_message.reply_text(
        "🎬 Записываю конференцию.\n"
        "Остановлю сама после ~5 минут тишины (когда все вышли), или пришли /stop.\n"
        "После — пришлю быстрый транскрипт, спрошу число участников и пришлю "
        "расшифровку со спикерами.")
    asyncio.create_task(_await_recorder(chat_id))


async def _await_recorder(chat_id: int) -> None:
    sess = SESSIONS.get(chat_id)
    if not sess:
        return
    await sess["proc"].wait()
    SESSIONS.pop(chat_id, None)
    log = ""
    try:
        log = Path(sess["log"]).read_text(errors="ignore")
    except Exception:
        pass

    def grab(marker: str) -> str | None:
        for line in log.splitlines():
            if line.startswith(marker):
                return line.split(":", 1)[1].strip()
        return None

    ogg = grab("RECORDING_SAVED")
    dur = grab("DURATION_SEC")
    reason = grab("STOP_REASON") or "?"
    if not ogg or not os.path.exists(ogg):
        await app.bot.send_message(chat_id, "❌ Запись не удалась — файл не найден.")
        _log.error("recording failed, log tail:\n%s", log[-1500:])
        return
    dur_i = int(dur) if dur and dur.isdigit() else 0
    sha = _sha256(ogg)
    PENDING[chat_id] = {"ogg": ogg, "dur": dur_i, "sha": sha}
    reason_h = {"ended": "звонок завершён", "empty": "все вышли",
                "silence": "5 мин тишины — считаю, завершён",
                "manual": "остановлено вручную", "maxcap": "лимит времени",
                "ffmpeg": "сбой записи"}.get(reason, reason)
    await app.bot.send_message(
        chat_id,
        f"✅ Запись завершена ({_fmt(dur_i)}, {reason_h}).\n"
        f"Сколько было участников? Ответь числом — прогоню диаризацию.")
    # Stage 1: fire off a fast Groq transcript right away, in parallel with the
    # participant-count question and the slow diarization (stage 2).
    # Once Groq returns the text it submits the short job directly (no whisper).
    asyncio.create_task(_send_quick_transcript(chat_id, ogg, sha))


async def _send_quick_transcript(chat_id: int, ogg: str, sha: str) -> None:
    """Stage 1: fast speaker-less transcript via Groq, sent ASAP after recording.

    The Groq text goes to Telegram only. It is NOT submitted to the pipeline
    right away: the downstream consumer dedupes by recording sha, so whichever job lands
    first is the one that reaches the classifier — and we want that to be the
    diarised mode=call job (stage 2). The short job is held back as a
    fallback (see _short_fallback) in case no participant count ever arrives.
    Best-effort — any failure is reported but never blocks the diarization
    stage, which still delivers the speaker-separated transcript later.
    """
    try:
        ph = await app.bot.send_message(chat_id, "⚡ Готовлю быстрый транскрипт (Groq)…")
    except Exception:
        ph = None
    try:
        text = await groq_quick_transcript(ogg)
    except Exception as e:
        _log.warning("groq quick transcript failed: %s", e)
        msg = "⚡ Быстрый транскрипт не получился — дождись расшифровки со спикерами (этап 2)."
        if ph is not None:
            try:
                await ph.edit_text(msg)
            except Exception:
                pass
        else:
            await app.bot.send_message(chat_id, msg)
        return
    if not text:
        if ph is not None:
            await ph.edit_text("⚡ В быстром проходе речь не распозналась 🤷 — будет этап 2.")
        return
    # Hold the short job back: it only goes to the pipeline if the participant
    # count never arrives (fallback), otherwise the diarised call job wins.
    pend = PENDING.get(chat_id)
    if pend is not None and pend.get("sha") == sha:
        pend["quick_text"] = text
        asyncio.create_task(_short_fallback(chat_id, ogg, sha))
    else:
        _log.info("quick transcript for %s: count already answered — short job skipped", chat_id)
    if len(text) <= 3500:
        body = "⚡ Быстрый транскрипт (Groq, без спикеров):\n\n" + text
        if ph is not None:
            await ph.edit_text(body)
        else:
            await app.bot.send_message(chat_id, body)
        return
    # too long for one message — deliver as a file
    fn = f"/tmp/quick_{chat_id}_{int(time.time())}.txt"
    Path(fn).write_text(text, encoding="utf-8")
    if ph is not None:
        try:
            await ph.edit_text("⚡ Быстрый транскрипт готов (файл ниже). Со спикерами придёт отдельно.")
        except Exception:
            pass
    with open(fn, "rb") as fh:
        await app.bot.send_document(
            chat_id, document=fh, filename=os.path.basename(fn),
            caption="⚡ Быстрый транскрипт (Groq, без спикеров)")


async def _short_fallback(chat_id: int, ogg: str, sha: str) -> None:
    """Submit the speaker-less short job only if no participant count arrived.

    Waits SHORT_FALLBACK_SEC; if the recording is still awaiting a count, the
    Groq text is submitted as mode=short so the call still reaches the classifier
    (without speakers). If the count was answered meanwhile, PENDING no longer
    holds this sha and nothing is submitted — the diarised job is the one
    the downstream consumer will see.
    """
    await asyncio.sleep(SHORT_FALLBACK_SEC)
    pend = PENDING.get(chat_id)
    if pend is None or pend.get("sha") != sha:
        return
    text = pend.get("quick_text")
    if not text:
        return
    try:
        uid, auth = _pipe_identity(chat_id)
        await _submit_short_job(ogg, sha, text, uid, auth)
    except Exception as e:
        _log.warning("_submit_short_job fallback failed: %s", e)
        return
    pend["short_submitted"] = True
    try:
        await app.bot.send_message(
            chat_id,
            "ℹ️ Число участников не пришло — отправил в ассистент быструю версию без спикеров. "
            "Ответишь числом — диаризацию всё равно прогоню и пришлю сюда.")
    except Exception:
        pass


async def _diarize_and_send(chat_id: int, ogg: str, n: int, sha: str | None = None) -> None:
    """Submit the recording for diarization, persist it as pending, then poll."""
    uid, auth = _pipe_identity(chat_id)
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            with open(ogg, "rb") as f:
                files = {"file": (os.path.basename(ogg), f, "application/octet-stream")}
                data = {"mode": "call", "user_id": uid, "num_speakers": str(n), "origin": "telemost"}
                if sha is not None:
                    data["dedupe_key"] = f"{sha}:call"
                r = await c.post(f"{PIPE_BASE}/jobs", headers=auth, files=files, data=data)
            r.raise_for_status()
            job = r.json()["job_id"]
    except Exception as e:
        await app.bot.send_message(chat_id, f"❌ Не смог отправить на распознавание: {e}")
        return
    # Persist BEFORE the long poll so a restart in the next hours re-attaches it.
    _pending_jobs_add(job, chat_id, n)
    await app.bot.send_message(
        chat_id,
        f"⏳ Отправил на распознавание ({n} спикеров). Пришлю, как будет готово.")
    await _poll_and_send(chat_id, job, n)


async def _poll_and_send(chat_id: int, job: str, n: int) -> None:
    """Poll an already-submitted diarization job to completion and deliver it.

    Safe to (re-)invoke for a job_id recovered from PENDING_JOBS_FILE after a
    restart — it just resumes polling. Clears the job from the pending store
    on terminal outcome (delivered or failed); a timeout leaves it persisted so
    a later restart can pick it back up.
    """
    # Use the launcher's auth — the job belongs to THEM, not the bot account.
    _, auth = _pipe_identity(chat_id)
    text = None
    async with httpx.AsyncClient(timeout=60) as c:
        for _ in range(int(6 * 3600 / 20)):
            await asyncio.sleep(20)
            # Disk is the source of truth. The transcript JSON is written the moment
            # whisper finishes and SURVIVES the downstream consumer deleting the job row from the
            # call-pipeline inbox (DELETE /jobs drops only the DB row, not the files).
            # Checking it first makes delivery immune to a delete race: the downstream consumer
            # polls drafted jobs and can delete a freshly-completed one ~2s after it
            # becomes "drafted" — before this 20s poll ever observes a deliverable
            # status — after which the old code spun the full 6h on 404s and reported
            # a false "longer than usual" while the result sat ready on disk.
            disk = _read_transcript_from_disk(job)
            if disk is not None:
                text = disk
                break
            try:
                jr = await c.get(f"{PIPE_BASE}/jobs/{job}", headers=auth)
            except Exception:
                continue
            if jr.status_code == 404:
                # Row gone AND nothing on disk → the result is genuinely lost.
                _pending_jobs_del(job)
                await app.bot.send_message(
                    chat_id,
                    "❌ Джоб распознавания исчез до доставки (удалён ingest-пайплайном), "
                    "транскрипт на диске не найден. Аудио сохранено — можно перезапустить.")
                return
            try:
                st = jr.json().get("status")
            except Exception:
                continue
            if st in ("transcribed", "drafted", "done"):
                tr = await c.get(f"{PIPE_BASE}/jobs/{job}/transcript", headers=auth)
                if tr.status_code == 200:
                    text = tr.json()
                    break
            if st and st.startswith("failed"):
                _pending_jobs_del(job)
                await app.bot.send_message(chat_id, f"❌ Распознавание не удалось ({st}).")
                return
    if not text:
        await app.bot.send_message(chat_id, "⌛ Распознавание идёт дольше обычного — проверь позже.")
        return
    out = _format_diarized(text)
    # Name the call from its own content so the caption tells you which call
    # this transcript is — otherwise every delivery looks identical. Best-effort:
    # a None title just falls back to the plain caption.
    flat = (text.get("text") if isinstance(text, dict) else None) or out
    title = await summarize_title(flat)
    fn = f"/tmp/transcript_{slugify(title, fallback=job[:8])}_{job[:8]}.txt"
    Path(fn).write_text(file_header(title) + out, encoding="utf-8")
    with open(fn, "rb") as fh:
        await app.bot.send_document(chat_id, document=fh, filename=os.path.basename(fn),
                                    caption=caption_for(title, n))
    _pending_jobs_del(job)


def _read_transcript_from_disk(job: str) -> dict | None:
    """Read the diarized transcript JSON straight from call-pipeline storage.

    The bot shares the pipeline host's filesystem, so it can read the transcript
    even after the downstream consumer deletes the job row from the inbox. The owning user dir
    is not necessarily PIPE_USER (recordings may be submitted under a different
    pipeline user), so locate the file by its unique job id across all user dirs.
    Returns the parsed payload, or None if the file is absent/unreadable.
    """
    try:
        tdir = storage.transcript_path(PIPE_USER, job).parent.parent  # .../transcripts
        for p in tdir.glob(f"*/{job}.json"):
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _log.warning("disk transcript read failed for %s: %s", job, e)
    return None


def _format_diarized(payload: dict) -> str:
    segs = payload.get("segments") or []
    lines, cur, buf, t0 = [], None, [], 0
    for s in segs:
        spk = s.get("speaker", "UNKNOWN")
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        if spk != cur:
            if buf:
                lines.append(f"[{_fmt(int(t0))}] {cur}: {' '.join(buf)}")
            cur, buf, t0 = spk, [txt], s.get("start", 0)
        else:
            buf.append(txt)
    if buf:
        lines.append(f"[{_fmt(int(t0))}] {cur}: {' '.join(buf)}")
    return "\n".join(lines) if lines else (payload.get("text") or "(пусто)")


# ── commands ────────────────────────────────────────────────────

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ensure_admin_menu(context.bot, _uid(update))
    if not _is_allowed(update):
        await _request_access(update, context)
        return
    await update.effective_message.reply_text(
        "Пришли ссылку на Телемост (или /record <ссылка>) — запишу конференцию, "
        "а после спрошу число участников и пришлю расшифровку со спикерами.")


async def record_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _request_access(update, context)
        return
    text = " ".join(context.args) if context.args else ""
    m = URL_RE.search(text)
    if not m:
        await update.effective_message.reply_text("Использование: /record <ссылка на Телемост>")
        return
    await _start_recording(update, m.group(0))


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    sess = SESSIONS.get(update.effective_chat.id)
    if not sess:
        await update.effective_message.reply_text("Нет активной записи.")
        return
    try:
        sess["proc"].terminate()
        await update.effective_message.reply_text("⏹ Останавливаю запись…")
    except Exception as e:
        await update.effective_message.reply_text(f"Не смог остановить: {e}")


async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    await _ensure_admin_menu(context.bot, _uid(update))
    if not _is_private(update):
        await update.effective_message.reply_text("Эта команда доступна только в личке.")
        return
    tid, u = _target(update, context)
    if tid is None:
        await update.effective_message.reply_text(
            "Чтобы выдать доступ — перешли сюда любое сообщение от этого человека.")
        return
    name = u.full_name if u else None
    username = u.username if u else None
    WL.add(tid, name=name, username=username)
    await update.effective_message.reply_text(f"✅ Доступ выдан: {_label(name, username, tid)}")


async def deny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    await _ensure_admin_menu(context.bot, _uid(update))
    if not _is_private(update):
        await update.effective_message.reply_text("Эта команда доступна только в личке.")
        return
    tid, u = _target(update, context)
    if tid is None:
        await update.effective_message.reply_text(
            "Чтобы забрать доступ — перешли сообщение человека или вызови /list.")
        return
    m = WL.meta(tid)
    WL.remove(tid)
    await update.effective_message.reply_text(
        f"🚫 Доступ забран: {_label(m.get('name'), m.get('username'), tid)}")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    await _ensure_admin_menu(context.bot, _uid(update))
    if not _is_private(update):
        await update.effective_message.reply_text("Эта команда доступна только в личке.")
        return
    wl = WL.all()
    lines = []
    for uid in wl:
        m = WL.meta(uid)
        lines.append("• " + html.escape(_label(m.get("name"), m.get("username"), uid)))
    body = "\n".join(lines) if lines else "(пусто)"
    admins = []
    for aid in sorted(ADMIN_IDS):
        m = WL.meta(aid)
        admins.append("• " + html.escape(_label(m.get("name"), m.get("username"), aid)))
    await update.effective_message.reply_text(
        f"📝 <b>Доступ есть у:</b>\n{body}\n\n👑 <b>Админы:</b>\n" + "\n".join(admins),
        parse_mode="HTML")


# ── access requests ────────────────────────────────────────────

async def _request_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u is None:
        return
    await update.effective_message.reply_text(
        "У вас нет доступа. Отправил запрос администраторам — дождись подтверждения.")
    if u.id in REQUESTS:
        return
    REQUESTS[u.id] = {"name": u.full_name, "username": u.username}
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить", callback_data=f"tgallow:{u.id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"tgdeny:{u.id}")]])
    label = _label(u.full_name, u.username, u.id)
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, f"🔔 Запрос доступа: {label}", reply_markup=kb)
        except Exception:
            pass


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Только для админов", show_alert=True)
        return
    data = q.data or ""
    if data.startswith("tgallow:"):
        uid = int(data.split(":")[1])
        info = REQUESTS.pop(uid, {})
        WL.add(uid, name=info.get("name"), username=info.get("username"))
        await q.edit_message_text(f"✅ Добавлен: {_label(info.get('name'), info.get('username'), uid)}")
        try:
            await context.bot.send_message(uid, "✅ Вам выдали доступ. Пришли ссылку на Телемост.")
        except Exception:
            pass
    elif data.startswith("tgdeny:"):
        uid = int(data.split(":")[1])
        REQUESTS.pop(uid, None)
        await q.edit_message_text("❌ Отклонено")
    await q.answer()


# ── text / forwards ────────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return
    chat_id = update.effective_chat.id

    # admin forwarded someone's message -> offer to add them (id-free)
    if _is_admin(update) and _is_private(update):
        fo = getattr(msg, "forward_origin", None)
        if isinstance(fo, MessageOriginUser):
            tu = fo.sender_user
            REQUESTS[tu.id] = {"name": tu.full_name, "username": tu.username}
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                f"✅ Добавить {tu.full_name}", callback_data=f"tgallow:{tu.id}")]])
            await msg.reply_text("Добавить этого человека в доступ?", reply_markup=kb)
            return
        if fo is not None:
            await msg.reply_text("У этого человека скрыта пересылка — пусть напишет боту сам, и я предложу добавить.")
            return

    text = (msg.text or "").strip()

    # awaiting participant count after a recording
    if chat_id in PENDING:
        if text.isdigit() and 1 <= int(text) <= 20:
            data = PENDING.pop(chat_id)
            asyncio.create_task(_diarize_and_send(chat_id, data["ogg"], int(text), sha=data.get("sha")))
            await msg.reply_text("Принял 👍")
        else:
            await msg.reply_text("Ответь числом участников (1–20).")
        return

    if not _is_allowed(update):
        await _request_access(update, context)
        return

    m = URL_RE.search(text)
    if m:
        await _start_recording(update, m.group(0))
    else:
        await msg.reply_text("Пришли ссылку на Телемост, чтобы начать запись.")


async def _post_init(application: Application) -> None:
    try:
        await application.bot.set_my_commands(PUBLIC_CMDS, scope=BotCommandScopeDefault())
        await application.bot.set_my_commands(PUBLIC_CMDS, scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        _log.warning("could not set public commands: %s", e)
    for aid in ADMIN_IDS:
        await _ensure_admin_menu(application.bot, aid)
    # Re-attach pollers for diarization jobs left in-flight across a restart.
    recovered = _pending_jobs()
    if recovered:
        _log.info("re-attaching %d pending diarization poller(s)", len(recovered))
    for job_id, info in recovered.items():
        try:
            chat_id = int(info["chat_id"])
            n = int(info.get("n", 0))
        except (KeyError, ValueError, TypeError):
            _pending_jobs_del(job_id)
            continue
        asyncio.create_task(_poll_and_send(chat_id, job_id, n))


app: Application = None  # set in main()


def main() -> None:
    global app
    app = Application.builder().token(TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("record", record_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("allow", allow_cmd))
    app.add_handler(CommandHandler("deny", deny_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^tg(allow|deny):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, on_message))
    _log.info("telemost control bot starting; admins=%s whitelist=%d",
              sorted(ADMIN_IDS), len(WL.all()))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
