import subprocess
import pytest
from app.lib.whisper_engine import transcribe


@pytest.fixture(scope="module")
def silence_3s(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures") / "silence_3s.wav"
    subprocess.run(
        ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "3", "-y", str(out)],
        capture_output=True, check=True, timeout=30,
    )
    return out


@pytest.mark.slow
def test_transcribe_silence_returns_empty_or_minimal(silence_3s):
    result = transcribe(silence_3s, language="ru")
    assert result["language"] == "ru"
    assert isinstance(result["segments"], list)
    # Silence should produce 0 or 1 short hallucinated segments
    assert len(result["segments"]) <= 2
    assert result["duration_sec"] >= 2.5
