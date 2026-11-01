"""`pipeline watch` — long-running poller that auto-uploads new call recordings.

Monitors one or more folders (default: ~/Downloads and ~/Downloads/Telegram Desktop).
For every fresh audio/video file it sees:
  1. Wait until the file is "stable" (mtime hasn't changed for --min-age seconds —
     so we don't grab files that are still being written by Zoom/Telemost/etc.).
  2. SHA256 the original file path; if we've already uploaded it (state file
     records the resulting job_id), skip.
  3. ffmpeg-extract audio if video container, then `pipeline upload` it.
  4. Persist {sha256 → job_id, mode, uploaded_at} to the state file.

State file lives in platformdirs `user_state_dir("call-pipeline")/watch_state.json`.
The state is purely a local-cache; server-side dedupe_key (also SHA256) provides
the real idempotency guarantee — even if state is lost, a re-upload returns the
existing job_id without creating a duplicate job.

Loop catches KeyboardInterrupt cleanly. Errors on individual files are logged
and the loop continues.

Run via Windows Task Scheduler for "always-on" behavior — see install-watcher.ps1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import click
from platformdirs import user_state_dir

from ..api_client import APIClient, APIError
from ..audio_extract import extract_audio, is_video_container
from ..config import load_config

AUDIO_EXTS = {
    ".webm", ".mp4", ".mov", ".mkv", ".avi",     # video → audio-extract
    ".mp3", ".m4a", ".wav", ".opus", ".ogg", ".aac", ".flac",  # audio
}

# Camera-roll / phone media that lands in Downloads & Telegram Desktop but is
# never a call recording. iPhone uses IMG_####.MOV/.MP4/.HEIC; Android camera and
# many gallery apps use VID_####. We never want these in the call pipeline, so
# skip them at scan time regardless of container. Matched case-insensitively
# against the filename stem.
EXCLUDE_NAME_RE = re.compile(r"^(?:IMG|VID)[_-]?\d+", re.IGNORECASE)

DEFAULT_FOLDERS = [
    Path.home() / "Downloads",
    Path.home() / "Downloads" / "Telegram Desktop",
]


def origin_for(path: Path) -> str:
    """Класс происхождения файла по папке, из которой его взял вотчер.

    Ловит стоп «это не звонок владельца, а присланный материал»: Zoom-папка —
    заведомо своя запись, Telegram Desktop — почти всегда чужая. Downloads
    неоднозначен: туда падают и скачанные Zoom-записи, и присланные файлы.
    """
    parts = [p.lower() for p in path.parts]
    if "zoom" in parts:
        return "zoom"
    if "telegram desktop" in parts:
        return "telegram-desktop"
    if "downloads" in parts:
        return "downloads"
    return "unknown"


def recorded_date_of(path: Path) -> str:
    """Запасная дата записи — mtime исходного файла, ISO 'YYYY-MM-DD'.

    Дату записи сервер достаёт из имени файла, но имя её несёт только у
    рекордера Телемоста: zoom-выгрузки и скачанные из телеграма файлы
    называются `video1855516632.opus`, и звонок остаётся в очереди разбора
    без даты вовсе. mtime у таких файлов — момент скачивания/записи, то есть
    настоящая дата с точностью до дня; имя, если дата в нём есть, всё равно
    авторитетнее (сервер берёт запасную только когда имя молчит).

    Считаем по ИСХОДНОМУ файлу, а не по извлечённому аудио: у временного
    .opus mtime — это момент работы ffmpeg, то есть всегда «сегодня».
    """
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


VIDEO_EXTS = {".webm", ".mp4", ".mov", ".mkv", ".avi"}


def prefer_audio(paths: list[Path]) -> list[Path]:
    """Одна zoom-встреча пишется и в аудио, и в видео. Два прогона whisper дают
    РАЗНЫЕ тексты, поэтому дедуп по содержимому не срабатывает — отсекаем здесь.
    Zoom именует пару audio<id>.m4a / video<id>.mp4 — префикс в ключ не входит.
    """
    def twin_key(p: Path) -> str:
        return (p.parent / re.sub(r"^(audio|video)", "", p.stem, flags=re.I)).as_posix()

    audio_keys = {twin_key(p) for p in paths if p.suffix.lower() not in VIDEO_EXTS}
    return [p for p in paths
            if p.suffix.lower() not in VIDEO_EXTS
            or twin_key(p) not in audio_keys]


# Короткие mp4/mov — это сгенерированные ролики (мультики Искры, Veo/Flux/Kling,
# пробы озвучки), а не звонки: 23–24.10.2026 за сутки уехало 12 таких на whisper
# и классификатор. Записи встреч Телемоста приходят .webm и бывают короткими —
# их не трогаем; аудио тоже.
SHORT_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
MIN_VIDEO_SEC = 300


def probe_duration(path: Path) -> float | None:
    """Длительность файла в секундах через ffprobe; None, если не вышло."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def is_short_video(path: Path) -> bool:
    """Видео короче MIN_VIDEO_SEC. Не смогли измерить — не отсекаем."""
    if path.suffix.lower() not in SHORT_VIDEO_EXTS:
        return False
    dur = probe_duration(path)
    return dur is not None and dur < MIN_VIDEO_SEC


def _state_path() -> Path:
    return Path(user_state_dir("call-pipeline")) / "watch_state.json"


def _load_state() -> dict:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_stable(path: Path, min_age_sec: int) -> bool:
    """File hasn't been modified in `min_age_sec` seconds."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    return (time.time() - mtime) >= min_age_sec


def _is_candidate(p: Path) -> bool:
    """True if this file should be considered for upload (right extension and
    not a camera-roll/phone-media file like IMG_1234.MOV)."""
    return (
        p.is_file()
        and p.suffix.lower() in AUDIO_EXTS
        and not EXCLUDE_NAME_RE.match(p.stem)
    )


def _scan(folder: Path, recursive: bool = False) -> list[Path]:
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if _is_candidate(p)]
    if recursive:
        # Depth=1 only: scan direct subfolders, not the whole tree.
        # Matches Zoom's "Documents/Zoom/<meeting>/audio.m4a" layout without
        # recursing into arbitrary deep trees (e.g. avoid surprises if someone
        # ever points this at Downloads).
        for sub in folder.iterdir():
            if sub.is_dir():
                try:
                    files.extend(p for p in sub.iterdir() if _is_candidate(p))
                except (PermissionError, OSError):
                    continue
    return files


def _process_one(
    src: Path, *, mode: str, cfg, state: dict, keep_extracted: bool
) -> str | None:
    """Try to upload one file. Returns the job_id on success, None on skip/fail."""
    sha = _sha256(src)
    if sha in state and state[sha].get("job_id"):
        return None  # already uploaded — skip silently

    target = src
    extracted: Path | None = None
    if is_video_container(src):
        try:
            extracted = extract_audio(src)
            target = extracted
        except Exception as e:
            click.echo(f"  ✗ ffmpeg extract failed: {e}", err=True)
            state[sha] = {"job_id": None, "error": f"extract: {e}",
                           "src": str(src), "tried_at": _now_iso()}
            _save_state(state)
            return None

    # Use the source-file SHA as dedupe_key so re-runs (even after state loss)
    # return the existing server-side job_id rather than re-transcribing.
    upload_dedupe = sha
    client = APIClient(base_url=cfg.server_url, token=cfg.bearer_token)
    try:
        # origin — по ИСХОДНОМУ пути src, а не target: у извлечённого ffmpeg'ом
        # временного аудио-файла папка уже другая, признак был бы потерян.
        result = client.upload(
            target, mode=mode, user_id=cfg.user_id, dedupe_key=upload_dedupe,
            origin=origin_for(src), fallback_recorded_at=recorded_date_of(src),
        )
    except APIError as e:
        click.echo(f"  ✗ upload failed: {e}", err=True)
        if extracted and not keep_extracted:
            extracted.unlink(missing_ok=True)
        return None
    finally:
        if extracted and not keep_extracted and target == extracted:
            # delete the extracted opus only after successful POST
            pass    # cleanup below

    if extracted and not keep_extracted:
        extracted.unlink(missing_ok=True)

    state[sha] = {
        "job_id": result["job_id"],
        "status": result["status"],
        "mode": mode,
        "src": str(src),
        "uploaded_at": _now_iso(),
    }
    _save_state(state)
    return result["job_id"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@click.command(name="watch")
@click.argument("folders", nargs=-1, type=click.Path(file_okay=False, path_type=Path))
@click.option("--recursive-folder", "recursive_folders", multiple=True,
              type=click.Path(file_okay=False, path_type=Path),
              help="Folder to scan recursively at depth=1 (root + direct subfolders). "
                   "For Zoom-style layouts like Documents/Zoom/<meeting>/audio.m4a. "
                   "Can be repeated.")
@click.option("--mode", type=click.Choice(["call", "short"]), default="call",
              help="Whisper queue (default: call, with diarize).")
@click.option("--poll-interval", type=int, default=30,
              help="Seconds between scans of all folders (default: 30).")
@click.option("--min-age", type=int, default=60,
              help="A file must be unchanged for this many seconds before upload "
                   "(default: 60). Avoids grabbing files still being written.")
@click.option("--once", is_flag=True,
              help="Scan once and exit (instead of looping forever). Useful for "
                   "Task Scheduler jobs that fire on a schedule.")
@click.option("--seed-only", is_flag=True,
              help="Mark every currently-present file as already-seen, write state, "
                   "and exit WITHOUT uploading anything. Run this once on first "
                   "install so the watcher doesn't pull the existing backlog.")
@click.option("--keep-extracted/--delete-extracted", default=False)
def watch(folders: tuple[Path, ...], recursive_folders: tuple[Path, ...],
          mode: str, poll_interval: int,
          min_age: int, once: bool, seed_only: bool, keep_extracted: bool):
    """Watch folders and auto-upload new audio/video files."""
    cfg = load_config()
    flat_list = list(folders) if (folders or recursive_folders) else list(DEFAULT_FOLDERS)
    # valid is a list of (folder, recursive) pairs
    valid: list[tuple[Path, bool]] = [(f, False) for f in flat_list if f.is_dir()]
    valid.extend((f, True) for f in recursive_folders if f.is_dir())
    if not valid:
        raise click.ClickException(
            "No valid folders to watch. Defaults are "
            f"{[str(f) for f in DEFAULT_FOLDERS]}."
        )
    click.echo(
        f"Watching {len(valid)} folder(s) every {poll_interval}s "
        f"(min-age {min_age}s, mode={mode}). State: {_state_path()}"
    )
    for f, rec in valid:
        click.echo(f"  · {f}" + ("  [recursive]" if rec else ""))

    state = _load_state()

    if seed_only:
        seeded = 0
        for folder, rec in valid:
            for src in _scan(folder, recursive=rec):
                try:
                    size = src.stat().st_size
                except FileNotFoundError:
                    continue
                fast_key = f"{src}|{size}"
                if fast_key not in state.get("_seen_paths", {}):
                    state.setdefault("_seen_paths", {})[fast_key] = None
                    seeded += 1
        _save_state(state)
        click.echo(f"Seeded {seeded} existing file(s) as already-seen. "
                   "Future `pipeline watch` runs will only upload genuinely new ones.")
        return
    stop = False

    def _on_signal(sig, frame):
        nonlocal stop
        click.echo("\nStopping…")
        stop = True

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    while not stop:
        new_count = 0
        for folder, rec in valid:
            candidates = _scan(folder, recursive=rec)
            if rec:
                # Рекурсивные папки — Zoom-стиль layout, где одна встреча часто
                # лежит и аудио- и видео-файлом с одинаковым stem. Берём аудио.
                candidates = prefer_audio(candidates)
            for src in candidates:
                if stop:
                    break
                if not _is_stable(src, min_age):
                    continue
                # Quick skip without hashing — if we've seen this exact path with
                # this exact size+mtime before, definitely don't re-hash a 1 GB
                # webm. We still do the SHA for upload, but only if it looks new.
                try:
                    size = src.stat().st_size
                except FileNotFoundError:
                    continue
                fast_key = f"{src}|{size}"
                if fast_key in state.get("_seen_paths", {}):
                    continue
                if is_short_video(src):
                    click.echo(f"[{_now_iso()}] skip {src.name} — short video, not a call")
                    state.setdefault("_seen_paths", {})[fast_key] = None
                    _save_state(state)
                    continue
                click.echo(f"[{_now_iso()}] uploading {src.name} ({size // (1024*1024)} MB)…")
                job_id = _process_one(
                    src, mode=mode, cfg=cfg, state=state,
                    keep_extracted=keep_extracted,
                )
                if job_id:
                    new_count += 1
                    click.echo(f"  ✓ job: {job_id}")
                    state.setdefault("_seen_paths", {})[fast_key] = job_id
                    _save_state(state)
                else:
                    # mark seen so we don't keep retrying every scan
                    state.setdefault("_seen_paths", {})[fast_key] = None
                    _save_state(state)
        if once:
            break
        if not stop:
            time.sleep(poll_interval)
    click.echo(f"Stopped. Processed {sum(1 for v in state.values() if isinstance(v, dict) and v.get('job_id'))} files total.")
