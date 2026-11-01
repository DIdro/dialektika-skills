import logging
import time
from sqlalchemy.orm import Session
from ..db import queries as q
from ..db import session as session_mod
from ..db.models import Job
from ..lib.whisper_engine import transcribe
from ._common import write_transcript, fail_job

POLL_INTERVAL_SEC = 2.0


def process_one() -> bool:
    """Try to grab one job and process it. Returns True if processed (success or retry).

    Claims both `short` (short clips) and `voice` (Telegram voice-bot)
    jobs — same loaded faster-whisper model, no diarization. `voice` jobs are
    not claimed by the classifier, so they terminate at status `transcribed`.
    """
    s: Session = session_mod.SessionLocal()
    try:
        job = q.claim_next_job(s, modes=["short", "voice"], from_status="uploaded",
                                to_status="processing_whisper")
        if job is None:
            return False
        try:
            q.record_event(s, job.id, event="started", stage="whisper")
            result = transcribe(job.audio_path, language=job.language)
            tpath = write_transcript(
                job, language=result["language"],
                duration=result["duration_sec"],
                segments=result["segments"],
                diarized=False,
            )
            q.mark_status(s, job.id, "transcribed")
            j2 = s.get(Job, job.id)
            j2.transcript_path = tpath
            j2.audio_duration_sec = result["duration_sec"]
            s.commit()
            q.record_event(s, job.id, event="completed", stage="whisper")
            return True
        except Exception as e:
            fail_job(s, job.id, stage="whisper", error=str(e))
            return True
    finally:
        s.close()


def recover_stranded() -> None:
    """См. whisper_call.recover_stranded — та же дыра, свои режимы."""
    s: Session = session_mod.SessionLocal()
    try:
        ids = q.requeue_stranded(s, modes=["short", "voice"], from_status="processing_whisper",
                                 to_status="uploaded", terminal_status="failed_whisper",
                                 stage="whisper")
        if ids:
            logging.getLogger(__name__).warning(
                "requeued %d job(s) stranded by a previous worker: %s", len(ids), ", ".join(ids))
    finally:
        s.close()


def run_loop():
    recover_stranded()
    while True:
        if not process_one():
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    run_loop()
