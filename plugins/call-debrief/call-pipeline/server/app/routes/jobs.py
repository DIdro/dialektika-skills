import json
import logging
import os
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import queries as q
from ..db.models import Job, User
from ..deps import current_user, get_db
from ..lib import storage
from ..lib.ffmpeg_utils import FFmpegError, extract_audio
from ..lib.filename_date import parse_recorded_at

router = APIRouter()
log = logging.getLogger(__name__)
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
ALLOWED_MODES = ("short", "call", "voice")
# Папка-источник со стороны вотчера — признак «свой звонок vs присланный материал»
# для гейта очереди приёмки (см. Task 10). Не доверяем клиенту произвольную строку.
ALLOWED_ORIGINS = {"zoom", "downloads", "telegram-desktop", "web", "telemost", "unknown"}
VIDEO_EXTS = {"mp4", "mkv", "mov", "avi", "m4v", "webm"}


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    mode: Annotated[str, Form()],
    user_id: Annotated[str, Form()],
    file: UploadFile = File(...),
    language: Annotated[str, Form()] = "ru",
    diarize: Annotated[bool | None, Form()] = None,
    num_speakers: Annotated[int | None, Form()] = None,
    origin: Annotated[str | None, Form()] = None,
    dedupe_key: Annotated[str | None, Form()] = None,
    recorded_at: Annotated[str | None, Form()] = None,
    fallback_recorded_at: Annotated[str | None, Form()] = None,
    transcript: Annotated[str | None, Form()] = None,
):
    if mode not in ALLOWED_MODES:
        raise HTTPException(400, f"mode must be one of {ALLOWED_MODES}")
    if user_id != user.id:
        raise HTTPException(403, "user_id mismatch with token")

    if dedupe_key:
        existing = q.find_job_by_dedupe(db, user.id, dedupe_key)
        if existing and existing.status not in ("failed_whisper", "failed_classifier"):
            response.status_code = 200
            return {"job_id": existing.id, "status": existing.status, "deduped": True}

    if diarize is None:
        diarize = (mode == "call")

    job_id = str(uuid.uuid4())
    storage.ensure_user_dirs(user.id)
    ext = (file.filename or "audio").rsplit(".", 1)[-1].lower() or "bin"
    audio_p = storage.audio_path(user.id, job_id, ext)

    written = 0
    with audio_p.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_BYTES:
                fh.close()
                audio_p.unlink(missing_ok=True)
                raise HTTPException(413, "file exceeds 1 GB limit")
            fh.write(chunk)

    # Видео — извлекаем аудио на сервере (веб-клиент шлёт файл как есть,
    # ffmpeg-извлечение на клиенте есть только у CLI)
    if ext in VIDEO_EXTS:
        ogg_p = audio_p.with_suffix(".ogg")
        try:
            # subprocess.run блокирует — уводим в тред, чтобы не вешать event-loop
            await run_in_threadpool(extract_audio, audio_p, ogg_p)
        except FFmpegError as e:
            audio_p.unlink(missing_ok=True)
            ogg_p.unlink(missing_ok=True)
            # детали (пути/stderr) — только в лог, клиенту общее сообщение
            log.warning("audio extraction failed for job %s: %s", job_id, e)
            raise HTTPException(422, "cannot extract audio from the uploaded video")
        audio_p.unlink(missing_ok=True)
        audio_p = ogg_p
        written = audio_p.stat().st_size

    # recorded_at: явный параметр, иначе дата из имени файла, иначе — запасная
    # дата от клиента (у CLI это mtime исходного файла: у zoom-выгрузок и
    # скачанных записей даты в имени нет вовсе, а без даты строка в очереди
    # разбора отображается пустой и звонок в ней не найти). Запасную гоняем через
    # тот же parse_recorded_at — он заодно и валидатор: мусор из формы в колонку
    # не попадёт. Осознанно НЕ подставляем дату загрузки на сервере: для старых
    # записей, залитых пачкой, она соврала бы, а recorded_at считается настоящей
    # датой звонка (по ней сервис разбора решает, не протух ли контекст).
    if not recorded_at:
        recorded_at = (parse_recorded_at(file.filename)
                       or parse_recorded_at(fallback_recorded_at))

    # origin: клиент может прислать что угодно — сваливаем неизвестное в unknown,
    # чтобы в колонке не оказалось произвольной строки
    origin = origin if origin in ALLOWED_ORIGINS else "unknown"

    # If a pre-computed transcript (e.g. Groq fast-pass) is supplied, write it
    # immediately so the job is ready for downstream consumers without whisper re-transcription.
    transcript_p = None
    if transcript and transcript.strip():
        transcript_p = storage.transcript_path(user.id, job_id)
        transcript_p.parent.mkdir(parents=True, exist_ok=True)
        transcript_p.write_text(
            json.dumps({"text": transcript}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    job_status = "transcribed" if transcript_p else "uploaded"
    job = Job(
        id=job_id, user_id=user.id, status=job_status,
        mode=mode, language=language, diarize=diarize,
        num_speakers=num_speakers,
        dedupe_key=dedupe_key, audio_path=str(audio_p),
        original_filename=file.filename, origin=origin,
        audio_size_bytes=written, recorded_at=recorded_at,
        transcript_path=str(transcript_p) if transcript_p else None,
    )
    db.add(job)
    db.commit()
    q.record_event(db, job_id, event="enqueued", stage="whisper")
    return {"job_id": job_id, "status": job_status}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: Annotated[User, Depends(current_user)],
            db: Annotated[Session, Depends(get_db)]):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.user_id != user.id:
        raise HTTPException(403, "not your job")
    return {
        "job_id": job.id, "status": job.status, "mode": job.mode,
        "language": job.language, "diarize": job.diarize,
        "num_speakers": job.num_speakers,
        "dedupe_key": job.dedupe_key,
        "audio_duration_sec": job.audio_duration_sec,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "recorded_at": job.recorded_at,
        "original_filename": job.original_filename,
        "origin": job.origin,
    }


@router.get("/jobs/{job_id}/transcript")
def get_transcript(job_id: str, user: Annotated[User, Depends(current_user)],
                   db: Annotated[Session, Depends(get_db)]):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.user_id != user.id:
        raise HTTPException(403, "not your job")
    if not job.transcript_path or job.status == "uploaded":
        raise HTTPException(404, "transcript not ready")
    with open(job.transcript_path, encoding="utf-8") as fh:
        return json.loads(fh.read())


@router.get("/jobs/{job_id}/audio")
def download_audio(job_id: str, user: Annotated[User, Depends(current_user)],
                   db: Annotated[Session, Depends(get_db)]):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.user_id != user.id:
        raise HTTPException(403, "not your job")
    if not job.audio_path or not os.path.isfile(job.audio_path):
        raise HTTPException(404, "audio file not found on disk")
    name = job.original_filename or os.path.basename(job.audio_path)
    return FileResponse(job.audio_path, filename=name,
                        media_type="application/octet-stream")


@router.get("/jobs/{job_id}/draft")
def get_draft(job_id: str, user: Annotated[User, Depends(current_user)],
              db: Annotated[Session, Depends(get_db)]):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.user_id != user.id:
        raise HTTPException(403, "not your job")
    if not job.draft_path:
        raise HTTPException(404, "draft not ready")
    with open(job.draft_path, encoding="utf-8") as fh:
        return json.loads(fh.read())


@router.get("/jobs")
def list_jobs(user: Annotated[User, Depends(current_user)],
              db: Annotated[Session, Depends(get_db)],
              status: str | None = None, limit: int = 50):
    stmt = select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Job.status == status)
    jobs = db.scalars(stmt).all()
    return {"jobs": [{"job_id": j.id, "status": j.status, "mode": j.mode,
                       "dedupe_key": j.dedupe_key,
                       "original_filename": j.original_filename,
                       "error_message": j.error_message,
                       "audio_duration_sec": j.audio_duration_sec,
                       "recorded_at": j.recorded_at,
                       "origin": j.origin,
                       "created_at": j.created_at.isoformat()} for j in jobs]}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, user: Annotated[User, Depends(current_user)],
               db: Annotated[Session, Depends(get_db)]):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.user_id != user.id:
        raise HTTPException(403, "not your job")
    db.delete(job)
    db.commit()
    return {"deleted": True}
