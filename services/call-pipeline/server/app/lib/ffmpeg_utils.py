import json
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError): ...


def probe_duration(path: str | Path) -> float:
    p = Path(path)
    if not p.exists():
        raise FFmpegError(f"file not found: {p}")
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(p)],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        raise FFmpegError(f"ffprobe failed: {e.stderr}") from e
    return float(json.loads(out.stdout)["format"]["duration"])


def extract_audio(src: str | Path, dst: str | Path) -> None:
    """Видео → mono opus 24k (тот же формат, что CLI audio_extract)."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vn",
             "-ac", "1", "-c:a", "libopus", "-b:a", "24k", str(dst)],
            check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as e:
        raise FFmpegError(f"ffmpeg failed: {e.stderr[-2000:]}") from e
    except subprocess.TimeoutExpired as e:
        # TimeoutExpired не наследует CalledProcessError — иначе утекло бы как 500
        raise FFmpegError(f"ffmpeg timed out after {e.timeout}s") from e


def to_whisper_wav(src: str | Path, dst: str | Path) -> None:
    """Normalize input to 16kHz mono WAV (whisper's preferred format)."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
             "-vn", "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, text=True, check=True, timeout=600,
        )
    except subprocess.CalledProcessError as e:
        raise FFmpegError(f"ffmpeg failed: {e.stderr}") from e
