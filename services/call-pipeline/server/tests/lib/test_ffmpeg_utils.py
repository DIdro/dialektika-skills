import subprocess
import pytest
from app.lib.ffmpeg_utils import probe_duration, to_whisper_wav, FFmpegError


@pytest.fixture(scope="module")
def silence_3s(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures") / "silence_3s.wav"
    subprocess.run(
        ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "3", "-y", str(out)],
        capture_output=True, check=True, timeout=30,
    )
    return out


def test_probe_duration(silence_3s):
    d = probe_duration(silence_3s)
    assert 2.9 < d < 3.1


def test_to_whisper_wav_passthrough(silence_3s, tmp_path):
    out = tmp_path / "out.wav"
    to_whisper_wav(silence_3s, out)
    assert out.exists() and out.stat().st_size > 0
    assert 2.9 < probe_duration(out) < 3.1


def test_probe_missing_file_raises(tmp_path):
    with pytest.raises(FFmpegError):
        probe_duration(tmp_path / "nope.wav")
