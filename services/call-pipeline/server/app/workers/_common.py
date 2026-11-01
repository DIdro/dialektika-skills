import json
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from ..db import queries as q
from ..db.models import Job
from ..lib import storage


def write_transcript(job: Job, *, language: str, duration: float,
                     segments: list[dict], diarized: bool) -> str:
    out_path = storage.transcript_path(job.user_id, job.id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = " ".join(s["text"] for s in segments).strip()
    payload = {
        "job_id": job.id,
        "language": language,
        "duration_sec": duration,
        "diarized": diarized,
        "text": text,
        "segments": segments,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return str(out_path)


def fail_job(s: Session, job_id: str, *, stage: str, error: str,
             max_retries: int = 3) -> None:
    n = q.increment_retry(s, job_id)
    if n >= max_retries:
        terminal = "failed_whisper" if stage == "whisper" else "failed_classifier"
        q.mark_status(s, job_id, terminal, error=error)
        q.record_event(s, job_id, event="failed", stage=stage, error=error)
    else:
        q.mark_status(s, job_id,
                      "uploaded" if stage == "whisper" else "transcribed",
                      error=error)
        q.record_event(s, job_id, event="retry", stage=stage, error=error)
