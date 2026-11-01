from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from .models import User, Job, JobEvent, Recording


def find_user_by_token(s: Session, token: str) -> User | None:
    return s.scalar(select(User).where(User.bearer_token == token))


def find_job_by_dedupe(s: Session, user_id: str, dedupe_key: str) -> Job | None:
    return s.scalar(
        select(Job).where(Job.user_id == user_id, Job.dedupe_key == dedupe_key)
    )


def claim_next_job(s: Session, *, modes: list[str], from_status: str, to_status: str) -> Job | None:
    """Atomically pick one oldest job in given modes/status, flip to to_status, return it.

    Implementation: SELECT ... LIMIT 1 then UPDATE WHERE id=? AND status=from_status,
    re-fetch. SQLite has no SELECT FOR UPDATE; we rely on single-writer per worker
    (each worker process is single-threaded loop) plus the conditional UPDATE.
    """
    job = s.scalar(
        select(Job).where(Job.status == from_status, Job.mode.in_(modes)).order_by(Job.created_at).limit(1)
    )
    if job is None:
        return None
    res = s.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == from_status)
        .values(status=to_status, updated_at=datetime.utcnow())
    )
    s.commit()
    if res.rowcount == 0:
        return None  # someone else claimed it
    s.refresh(job)
    return job


def mark_status(s: Session, job_id: str, status: str, *, error: str | None = None) -> None:
    s.execute(
        update(Job).where(Job.id == job_id).values(
            status=status, error_message=error, updated_at=datetime.utcnow()
        )
    )
    s.commit()


def record_event(s: Session, job_id: str, *, event: str, stage: str, duration_ms: int | None = None, error: str | None = None) -> None:
    s.add(JobEvent(job_id=job_id, event=event, stage=stage, duration_ms=duration_ms, error=error))
    s.commit()


STRANDED_ERROR = "воркер был остановлен во время обработки"


def requeue_stranded(s: Session, *, modes: list[str], from_status: str,
                     to_status: str, terminal_status: str, stage: str,
                     max_retries: int = 3) -> list[str]:
    """Вернуть в очередь джобы, брошенные на полпути убитым воркером.

    Из processing_* есть ровно два выхода: успех и except внутри процесса.
    SIGTERM не даёт отработать ни тому, ни другому (рестарт при деплое,
    OOM-kill, ребут), и строка остаётся в processing_* навсегда:
    claim_next_job берёт только из from_status и мимо неё проходит.
    Прецедент 04.09.26 — звонок f9f099b7 попал под `systemctl restart` через
    53 минуты после старта расшифровки и провисел четверо суток, не подав ни
    одного признака: в очередь разбора такая джоба не приходит вовсе.

    Зовётся на старте воркера. В этот момент по его modes не работает никто
    (по одному процессу systemd на режим), поэтому перехватить чужую
    незаконченную работу нельзя.

    retry_count не даёт зациклиться: джоба, которая роняет воркер сама
    (например, OOM на длинном звонке), после max_retries уходит в
    терминальный статус, а не перезапускает его до бесконечности.
    """
    stranded = s.scalars(
        select(Job).where(Job.status == from_status, Job.mode.in_(modes))
    ).all()
    requeued, given_up = [], []
    for job in stranded:
        job.retry_count += 1
        job.updated_at = datetime.utcnow()
        if job.retry_count > max_retries:
            job.status = terminal_status
            job.error_message = STRANDED_ERROR
            given_up.append(job.id)
        else:
            job.status = to_status
            requeued.append(job.id)
    s.commit()
    # Событие пишем после коммита статусов: восстановление должно быть видно в
    # истории джобы, иначе она молча меняет статус спустя несколько суток.
    for job_id in requeued:
        record_event(s, job_id, event="requeued", stage=stage, error=STRANDED_ERROR)
    for job_id in given_up:
        record_event(s, job_id, event="failed", stage=stage, error=STRANDED_ERROR)
    return requeued


def increment_retry(s: Session, job_id: str) -> int:
    job = s.get(Job, job_id)
    job.retry_count += 1
    s.commit()
    return job.retry_count


# ── recordings ─────────────────────────────────────────────────────────────

def claim_next_recording(s: Session, *, from_status: str, to_status: str) -> Recording | None:
    """Atomically pick the oldest recording in from_status, flip to to_status.

    Same conditional-UPDATE pattern as claim_next_job; safe for the single
    worker-recorder loop. Returns None if nothing waiting / lost the race.
    """
    rec = s.scalar(
        select(Recording).where(Recording.status == from_status)
        .order_by(Recording.created_at).limit(1)
    )
    if rec is None:
        return None
    res = s.execute(
        update(Recording)
        .where(Recording.id == rec.id, Recording.status == from_status)
        .values(status=to_status, updated_at=datetime.utcnow())
    )
    s.commit()
    if res.rowcount == 0:
        return None
    s.refresh(rec)
    return rec


def active_recordings(s: Session) -> list[Recording]:
    """Recordings the worker is currently running (for restart recovery / cap)."""
    return list(s.scalars(
        select(Recording).where(Recording.status == "recording").order_by(Recording.created_at)
    ).all())


def recording_stop_requested(s: Session, rec_id: str) -> bool:
    rec = s.get(Recording, rec_id)
    return bool(rec and rec.stop_requested)
