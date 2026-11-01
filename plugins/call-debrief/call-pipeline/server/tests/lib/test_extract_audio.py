import shutil
import subprocess
import pytest
from app.lib.ffmpeg_utils import FFmpegError, extract_audio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="ffmpeg not installed")


def _make_test_video(path):
    # 1 секунда: чёрный экран + синус 440Гц
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "color=black:s=64x64:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def test_extract_audio_from_video(tmp_path):
    src = tmp_path / "in.mp4"
    _make_test_video(src)
    dst = tmp_path / "out.ogg"
    extract_audio(src, dst)
    assert dst.is_file() and dst.stat().st_size > 0


def test_extract_audio_garbage_raises(tmp_path):
    src = tmp_path / "garbage.mp4"
    src.write_bytes(b"not a video at all")
    with pytest.raises(FFmpegError):
        extract_audio(src, tmp_path / "out.ogg")
