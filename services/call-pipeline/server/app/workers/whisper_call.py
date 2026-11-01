import gc
import logging
import tempfile
import time
from pathlib import Path
from sqlalchemy.orm import Session
from ..db import queries as q
from ..db import session as session_mod
from ..db.models import Job
from ..lib.whisper_engine import transcribe
from ..lib.diarize_engine import diarize, merge_with_segments
from ..lib.ffmpeg_utils import to_whisper_wav
from ._common import write_transcript, fail_job

POLL_INTERVAL_SEC = 5.0


def process_one() -> bool:
    s: Session = session_mod.SessionLocal()
    try:
        job = q.claim_next_job(s, modes=["call"], from_status="uploaded",
                                to_status="processing_whisper")
        if job is None:
            return False
        try:
            q.record_event(s, job.id, event="started", stage="whisper")
            result = transcribe(job.audio_path, language=job.language)
            # Free whisper RAM before pyannote (sequential, not parallel)
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            gc.collect()

            spans = []
            if job.diarize:
                # diarize() требует 16kHz mono PCM s16le WAV; загруженное аудио
                # может быть opus/webm/mp4. Нормализуем через ffmpeg.
                with tempfile.NamedTemporaryFile(
                    suffix=".wav", delete=False
                ) as tmp:
                    tmp_wav = Path(tmp.name)
                try:
                    to_whisper_wav(job.audio_path, tmp_wav)
                    spans = diarize(tmp_wav, num_speakers=job.num_speakers)
                except Exception:
                    # Диаризация — дорогое, но НЕ обязательное обогащение. Whisper
                    # к этому моменту уже отработал; ронять джобу значит выбросить
                    # готовую расшифровку. Падаем в ветку UNKNOWN ниже.
                    logging.getLogger(__name__).exception(
                        "diarization failed for job %s, continuing without speakers", job.id)
                    spans = []
                finally:
                    tmp_wav.unlink(missing_ok=True)
            if spans:
                segs = merge_with_segments(result["segments"], spans)
            else:
                segs = [{**seg, "speaker": "UNKNOWN"} for seg in result["segments"]]

            tpath = write_transcript(
                job,
                language=result["language"],
                duration=result["duration_sec"],
                segments=segs,
                diarized=bool(spans),
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
    """Джобы, брошенные прошлым — убитым — воркером, вернуть в очередь.

    Зовётся на старте: по режиму "call" в этот момент не работает никто, так
    что перехватить чужую незаконченную работу нельзя.
    """
    s: Session = session_mod.SessionLocal()
    try:
        ids = q.requeue_stranded(s, modes=["call"], from_status="processing_whisper",
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
