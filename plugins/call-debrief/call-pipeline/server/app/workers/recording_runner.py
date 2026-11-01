"""worker-recorder — runs API/agent-requested Telemost recordings concurrently.

Claims `requested` recordings, launches the headless recorder for each with its
OWN DISPLAY + Pulse sink (concurrency-safe: Chromium routes by PULSE_SINK env,
validated), watches for a stop request or the natural silence auto-stop, then
submits a mode=call Job under the SAME user_id — so the speaker-diarized draft
lands in that user's the user's drafts, exactly like `pipeline upload`.

Up to RECORDER_MAX_CONCURRENT at once, each on display :(DISPLAY_BASE+slot).
The Telegram bot's own recorder (DISPLAY :99 / telemost_sink) is independent.
"""
import asyncio
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from ..db import queries as q
from ..db import session as session_mod
from ..db.models import Job, Recording
from ..telemost.quick_transcript import groq_quick_transcript, strip_hallucinations

POLL_SEC = 5
MAX_CONCURRENT = int(os.environ.get("RECORDER_MAX_CONCURRENT", "3"))
DISPLAY_BASE = int(os.environ.get("RECORDER_DISPLAY_BASE", "101"))
MAX_RECORD_SEC = int(os.environ.get("RECORDER_MAX_SEC", str(4 * 3600)))
SERVER_ROOT = str(Path(__file__).resolve().parents[2])  # /opt/call-pipeline/server

# rec_id -> {proc, display, sink, log, slot, user_id, num_speakers}
_running: dict[str, dict] = {}


def _grab(log: str, marker: str) -> str | None:
    for line in log.splitlines():
        if line.startswith(marker):
            return line.split(":", 1)[1].strip()
    return None


def _free_slot() -> int | None:
    used = {info["slot"] for info in _running.values()}
    for i in range(MAX_CONCURRENT):
        if i not in used:
            return i
    return None


def _launch(rec: Recording) -> None:
    slot = _free_slot()
    if slot is None:
        return
    display = f":{DISPLAY_BASE + slot}"
    sink = f"rec_{rec.id[:8]}"
    log_path = f"/tmp/recrun_{rec.id[:8]}.log"
    env = {**os.environ, "DISPLAY": display, "PULSE_SINK": sink,
           "PYTHONUNBUFFERED": "1"}
    fh = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.telemost.recorder", rec.url, str(MAX_RECORD_SEC)],
        stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=SERVER_ROOT)
    _running[rec.id] = {"proc": proc, "display": display, "sink": sink,
                        "log": log_path, "slot": slot, "user_id": rec.user_id,
                        "num_speakers": rec.num_speakers}
    s = session_mod.SessionLocal()
    try:
        r = s.get(Recording, rec.id)
        r.display, r.sink = display, sink
        s.commit()
    finally:
        s.close()
    print(f"[recrun] started {rec.id[:8]} on {display}/{sink}", flush=True)


def _quick_pass(rec_id: str, ogg: str) -> None:
    """Run the fast Groq transcript for a --quick recording and store it on the
    row. Runs in a daemon thread so it never blocks the worker loop."""
    try:
        text = strip_hallucinations(asyncio.run(groq_quick_transcript(ogg)))
    except Exception as e:
        print(f"[recrun] quick transcript {rec_id[:8]} failed: {e}", flush=True)
        return
    s = session_mod.SessionLocal()
    try:
        rec = s.get(Recording, rec_id)
        if rec is not None and text:
            rec.quick_transcript = text
            s.commit()
            print(f"[recrun] quick transcript {rec_id[:8]} ready ({len(text)} chars)", flush=True)
    finally:
        s.close()


def _finish(rec_id: str) -> None:
    info = _running.pop(rec_id)
    try:
        log = Path(info["log"]).read_text(errors="ignore")
    except Exception:
        log = ""
    ogg = _grab(log, "RECORDING_SAVED")
    dur = _grab(log, "DURATION_SEC")
    reason = _grab(log, "STOP_REASON") or "?"
    do_quick = False
    s = session_mod.SessionLocal()
    try:
        rec = s.get(Recording, rec_id)
        if rec is None:
            return
        if not ogg or not os.path.exists(ogg):
            rec.status = "failed"
            rec.error_message = f"recording produced no file ({reason})"
            s.commit()
            print(f"[recrun] {rec_id[:8]} FAILED — no file ({reason})", flush=True)
            return
        rec.audio_path = ogg
        rec.duration_sec = float(dur) if dur and dur.replace(".", "", 1).isdigit() else None
        rec.stop_reason = reason
        do_quick = bool(rec.quick)
        # submit a mode=call Job under the SAME user → draft to their inbox
        job = Job(
            id=str(uuid.uuid4()), user_id=rec.user_id, status="uploaded",
            mode="call", language="ru", diarize=True,
            num_speakers=rec.num_speakers, audio_path=ogg,
            audio_size_bytes=os.path.getsize(ogg),
            # origin обязателен: без него колонка остаётся NULL, сервис разбора схлопывает
            # её в "unknown", а гейт считает "unknown" признаком чужой записи — флаг
            # "похоже на присланный материал" вставал бы на КАЖДОЙ записи Телемоста.
            origin="telemost",
        )
        s.add(job)
        rec.job_id = job.id
        rec.status = "submitted"
        s.commit()
        q.record_event(s, job.id, event="enqueued", stage="whisper")
        print(f"[recrun] {rec_id[:8]} → job {job.id[:8]} ({reason}, {dur}s)", flush=True)
    finally:
        s.close()
    # fast Groq pass in the background (agent fetches it via the API — no Telegram)
    if do_quick:
        threading.Thread(target=_quick_pass, args=(rec_id, ogg), daemon=True).start()


def _recover_orphans() -> None:
    """A 'recording' row at startup means the worker died mid-capture; its
    recorder (a child process) went with it and the audio is lost. Mark failed."""
    s = session_mod.SessionLocal()
    try:
        for rec in q.active_recordings(s):
            rec.status = "failed"
            rec.error_message = "worker restarted mid-recording"
        s.commit()
    finally:
        s.close()


def run_loop() -> None:
    _recover_orphans()
    print(f"[recrun] worker-recorder up (max {MAX_CONCURRENT} concurrent)", flush=True)
    while True:
        # 1) service running recordings: process exits + stop requests
        for rec_id in list(_running):
            info = _running[rec_id]
            if info["proc"].poll() is not None:
                _finish(rec_id)
                continue
            s = session_mod.SessionLocal()
            try:
                if q.recording_stop_requested(s, rec_id):
                    print(f"[recrun] stop requested {rec_id[:8]}", flush=True)
                    info["proc"].terminate()
            finally:
                s.close()
        # 2) claim new while we have capacity
        while len(_running) < MAX_CONCURRENT:
            s = session_mod.SessionLocal()
            try:
                rec = q.claim_next_recording(s, from_status="requested",
                                             to_status="recording")
            finally:
                s.close()
            if rec is None:
                break
            # cancelled (stop) before it ever started?
            s = session_mod.SessionLocal()
            try:
                fresh = s.get(Recording, rec.id)
                if fresh.stop_requested:
                    fresh.status = "failed"
                    fresh.error_message = "cancelled before start"
                    s.commit()
                    continue
            finally:
                s.close()
            _launch(rec)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    run_loop()
