import subprocess
from pathlib import Path
from pipeline_cli.audio_extract import is_video_container, extract_audio


def test_is_video_container_by_ext():
    assert is_video_container(Path("a.webm"))
    assert is_video_container(Path("a.mp4"))
    assert is_video_container(Path("a.MOV"))
    assert is_video_container(Path("a.mkv"))
    assert not is_video_container(Path("a.mp3"))
    assert not is_video_container(Path("a.opus"))
    assert not is_video_container(Path("a.wav"))


def test_extract_audio(tmp_path):
    src = tmp_path / "in.webm"
    # Create a tiny webm with a sine tone
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:a", "libopus", str(src)],
        capture_output=True, check=True, timeout=30,
    )
    dst = extract_audio(src)
    assert dst.exists()
    assert dst.suffix == ".opus"
    assert dst.stat().st_size > 0


def test_extract_audio_avoids_overwriting_source_when_already_opus(tmp_path):
    # Edge case: caller passes a .opus that was misidentified as needing extraction.
    # extract_audio should not clobber the source.
    src = tmp_path / "x.opus"
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:a", "libopus", str(src)],
        capture_output=True, check=True, timeout=30,
    )
    dst = extract_audio(src)
    assert dst != src
    assert dst.exists()
