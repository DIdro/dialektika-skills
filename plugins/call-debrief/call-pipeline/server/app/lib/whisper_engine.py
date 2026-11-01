from pathlib import Path
from faster_whisper import WhisperModel

_MODEL: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    return _MODEL


def transcribe(audio_path: str | Path, *, language: str = "ru") -> dict:
    """Run faster-whisper on audio file; returns dict with language, segments, duration_sec."""
    model = _get_model()
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        # Anti-hallucination. Whisper was trained on mountains of video
        # subtitles, so on silence / trailing audio it invents subtitle-style
        # endings ("Продолжение следует...", "Спасибо за просмотр",
        # "Субтитры сделал ..."). Disabling cross-segment conditioning stops
        # the loop that repeats such a phantom once it appears; word
        # timestamps + a silence threshold let faster-whisper skip the quiet
        # gaps (>2s) where these phantoms are generated.
        condition_on_previous_text=False,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
    )
    segments = []
    for s in segments_iter:
        segments.append({
            "start": float(s.start),
            "end": float(s.end),
            "text": s.text.strip(),
        })
    return {
        "language": info.language,
        "duration_sec": float(info.duration),
        "segments": segments,
    }
