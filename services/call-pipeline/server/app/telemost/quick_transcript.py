"""Fast, speaker-less transcript via Groq Whisper (whisper-large-v3).

Stage 1 of the Telemost two-stage delivery: the moment a recording stops we
push the raw audio through Groq's LPU Whisper, which returns a full transcript
in seconds — long before the slow local diarization (stage 2) finishes. No
speakers, no cleanup; just something readable immediately.

Large files are split into time chunks with ffmpeg to stay under Groq's
per-file size limit, transcribed sequentially, then concatenated.

Reads GROQ_API_KEY from the environment (set via /etc/call-pipeline/env).
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
SIZE_LIMIT = 24 * 1024 * 1024     # split above this (Groq free-tier cap is 25 MB)
CHUNK_SEC = 15 * 60               # 15-min segments when splitting (~3-4 MB each)


async def _transcribe_one(client: httpx.AsyncClient, path: str) -> str:
    with open(path, "rb") as fh:
        files = {"file": (os.path.basename(path), fh, "application/octet-stream")}
        data = {"model": MODEL, "language": "ru",
                "response_format": "json", "temperature": "0"}
        headers = {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}
        r = await client.post(GROQ_URL, headers=headers, files=files, data=data)
    r.raise_for_status()
    return (r.json().get("text") or "").strip()


def _split(ogg: str) -> tuple[str, list[str]]:
    """ffmpeg-segment the audio into CHUNK_SEC pieces; return (tmpdir, chunks)."""
    tmpdir = tempfile.mkdtemp(prefix="groq_chunks_")
    pattern = os.path.join(tmpdir, "chunk_%03d.ogg")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", ogg,
         "-f", "segment", "-segment_time", str(CHUNK_SEC), "-c", "copy", pattern],
        check=True)
    chunks = [str(c) for c in sorted(Path(tmpdir).glob("chunk_*.ogg"))]
    return tmpdir, chunks


# Whisper tends to hallucinate stock phrases over trailing silence. Groq's API
# doesn't expose faster-whisper's anti-hallucination knobs, so we peel these off
# the END of the transcript instead.
_TAIL_HALLUCINATIONS = (
    "продолжение следует",
    "субтитры делал",
    "субтитры сделал",
    "субтитры",
    "спасибо за просмотр",
    "редактор субтитров",
    "korrektor",
    "продолжение в следующей части",
)


def strip_hallucinations(text: str) -> str:
    """Remove stock whisper hallucination phrases at the very end of a transcript.

    Two passes: peel phrases the text literally ends with (e.g. "Продолжение
    следует..."), then drop a trailing sentence that merely *contains* a marker
    (e.g. "Субтитры сделал DimaTorzok").
    """
    t = (text or "").strip()
    changed = True
    while changed:
        changed = False
        low = t.lower().rstrip(" .…!?-—\n")
        for h in _TAIL_HALLUCINATIONS:
            if low.endswith(h):
                t = t[:t.lower().rfind(h)].rstrip(" .…!?-—\n")
                changed = True
                break
    parts = re.split(r"(?<=[.!?…])\s+", t)
    while parts and any(h in parts[-1].lower() for h in _TAIL_HALLUCINATIONS):
        parts.pop()
    return " ".join(parts).strip()


async def groq_quick_transcript(ogg: str) -> str:
    """Return a flat transcript of the recording via Groq Whisper.

    Raises on hard failure (missing key, network, ffmpeg) — the caller decides
    how to surface that without blocking the diarization stage.
    """
    async with httpx.AsyncClient(timeout=300) as client:
        if os.path.getsize(ogg) <= SIZE_LIMIT:
            return await _transcribe_one(client, ogg)
        tmpdir, chunks = _split(ogg)
        try:
            parts = [await _transcribe_one(client, c) for c in chunks]
            return " ".join(p for p in parts if p).strip()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
