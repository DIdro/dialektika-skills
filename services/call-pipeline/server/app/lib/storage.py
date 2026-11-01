import os
from pathlib import Path


def _root() -> Path:
    return Path(os.environ.get("PIPELINE_STORAGE_ROOT", "/var/lib/call-pipeline"))


def audio_path(user_id: str, job_id: str, ext: str) -> Path:
    return _root() / "audio" / user_id / f"{job_id}.{ext}"


def transcript_path(user_id: str, job_id: str) -> Path:
    return _root() / "transcripts" / user_id / f"{job_id}.json"


def draft_path(user_id: str, job_id: str) -> Path:
    return _root() / "drafts" / user_id / f"{job_id}.json"


def ensure_user_dirs(user_id: str) -> None:
    for kind in ("audio", "transcripts", "drafts"):
        (_root() / kind / user_id).mkdir(parents=True, exist_ok=True)
