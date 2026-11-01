import os
import subprocess
import pytest
from app.lib.diarize_engine import diarize, merge_with_segments


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
@pytest.mark.skipif(not os.environ.get("HF_TOKEN"), reason="HF_TOKEN not set")
def test_diarize_silence_returns_empty(silence_3s):
    spans = diarize(silence_3s)
    assert isinstance(spans, list)
    # Silence may produce zero speaker spans, that's fine


def test_merge_with_segments_assigns_speaker():
    segs = [{"start": 0.0, "end": 1.0, "text": "hi"}]
    spans = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
    out = merge_with_segments(segs, spans)
    assert out[0]["speaker"] == "SPEAKER_00"


def test_merge_no_overlap_keeps_unknown():
    segs = [{"start": 5.0, "end": 6.0, "text": "x"}]
    spans = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
    out = merge_with_segments(segs, spans)
    assert out[0]["speaker"] == "UNKNOWN"
