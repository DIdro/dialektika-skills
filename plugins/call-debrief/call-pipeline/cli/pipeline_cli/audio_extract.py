import subprocess
from pathlib import Path

VIDEO_EXTS = {".webm", ".mp4", ".mov", ".mkv", ".avi"}


def is_video_container(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def extract_audio(src: Path) -> Path:
    """Extract audio track to .opus next to source. Caller can delete after upload.

    If source already has .opus extension, write to a sibling `<stem>.audio.opus`
    so we never clobber the original.
    """
    if src.suffix.lower() == ".opus":
        dst = src.with_name(src.stem + ".audio.opus")
    else:
        dst = src.with_suffix(".opus")

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn",
         "-c:a", "libopus", "-b:a", "64k", str(dst)],
        capture_output=True, check=True,
    )
    return dst
