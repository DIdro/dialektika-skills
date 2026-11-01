"""Pyannote 4.x diarization wrapper.

Ported from ops/transcribe_one.py — the canonical, working pyannote 4.x usage:
  - Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=...)
  - Custom WAV loader to bypass torchcodec/soundfile flakiness on Windows
  - DiarizeOutput.exclusive_speaker_diarization → Annotation.itertracks(yield_label=True)

torchaudio compat shim:
  speechbrain<1.1 (we pin it to dodge the k2 lazy-import on >=1.1) calls
  `torchaudio.list_audio_backends()` which torchaudio 2.10+ removed. Without
  the shim below, `from pyannote.audio import Pipeline` raises AttributeError
  during speechbrain init. The shim returns a benign single-backend list so
  speechbrain's check passes; pyannote's actual audio loading goes through
  our `_load_wav_for_pyannote` which uses stdlib `wave` directly anyway.
"""
import os
from pathlib import Path

import torchaudio

if not hasattr(torchaudio, "list_audio_backends"):
    torchaudio.list_audio_backends = lambda: ["soundfile"]

_PIPELINE = None


def _get_pipeline():
    """Lazy-load and cache the pyannote pipeline. Requires HF_TOKEN env var."""
    global _PIPELINE
    if _PIPELINE is None:
        from pyannote.audio import Pipeline

        token = os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError("HF_TOKEN required for pyannote diarization")
        _PIPELINE = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-community-1",
            token=token,
        )
    return _PIPELINE


def _load_wav_for_pyannote(wav_path: Path):
    """Read a PCM s16le WAV into a torch.Tensor (channels, time).

    Avoids torchcodec/soundfile failures we hit on Windows by using stdlib `wave`.
    Caller is responsible for ensuring the file is 16kHz mono PCM s16le
    (use ffmpeg_utils.to_whisper_wav upstream).
    """
    import wave
    import numpy as np
    import torch

    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        nframes = w.getnframes()
        sampwidth = w.getsampwidth()
        data = w.readframes(nframes)

    if sampwidth != 2:
        raise RuntimeError(
            f"unexpected sample width {sampwidth}, expected 2 (PCM s16le)"
        )

    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        audio = audio.reshape(-1, nch).T
    else:
        audio = audio[None, :]
    return torch.from_numpy(audio.copy()), sr


def diarize(audio_path: str | Path, num_speakers: int | None = None) -> list[dict]:
    """Run pyannote 4.x diarization on a WAV file.

    Args:
        audio_path: Path to 16kHz mono PCM s16le WAV file.
        num_speakers: Expected number of speakers. When provided, pyannote
            uses it as a hard constraint instead of estimating automatically.
            Reduces over-segmentation for calls with a known speaker count.

    Returns a chronologically-ordered list of speaker spans:
        [{"start": float, "end": float, "speaker": str}, ...]

    Empty list if the audio has no speech. Raises RuntimeError if HF_TOKEN
    is not set. Expects 16kHz mono PCM s16le WAV (run ffmpeg_utils first).
    """
    pipe = _get_pipeline()
    waveform, sr = _load_wav_for_pyannote(Path(audio_path))

    kwargs = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers

    output = pipe({"waveform": waveform, "sample_rate": sr}, **kwargs)

    # pyannote 4.x returns DiarizeOutput; exclusive_speaker_diarization
    # gives non-overlapping spans (right thing for downstream transcript
    # speaker assignment).
    annotation = output.exclusive_speaker_diarization
    spans: list[dict] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        spans.append({
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": speaker,
        })
    return spans


def merge_with_segments(segments: list[dict], spans: list[dict]) -> list[dict]:
    """Attach a speaker label to each whisper segment via max time-overlap.

    For each segment, picks the diarization span with the largest overlap.
    Segments with no overlapping span get "UNKNOWN".
    """
    out: list[dict] = []
    for seg in segments:
        best_speaker = "UNKNOWN"
        best_overlap = 0.0
        for sp in spans:
            ovl = min(seg["end"], sp["end"]) - max(seg["start"], sp["start"])
            if ovl > best_overlap:
                best_overlap = ovl
                best_speaker = sp["speaker"]
        out.append({**seg, "speaker": best_speaker})
    return out
