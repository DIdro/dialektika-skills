import httpx
import respx
from click.testing import CliRunner
from pathlib import Path
from pipeline_cli.cli import main


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text("""\
[server]
url = "https://x"
bearer_token = "t"
user_id = "u"
""")
    return cfg


@respx.mock
def test_upload_audio_no_extract(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    audio = tmp_path / "x.opus"
    audio.write_bytes(b"OggS\x00\x00\x00\x00")  # minimal ogg-ish bytes
    route = respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j", "status": "uploaded"})
    )
    r = CliRunner().invoke(main, ["upload", str(audio)])
    assert r.exit_code == 0, r.output
    assert "j" in r.output
    assert route.called


@respx.mock
def test_upload_video_extracts_audio(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    video = tmp_path / "in.webm"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1",
         "-c:a", "libopus", str(video)],
        capture_output=True, check=True, timeout=30,
    )
    route = respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j2", "status": "uploaded"})
    )
    r = CliRunner().invoke(main, ["upload", str(video)])
    assert r.exit_code == 0, r.output
    assert "j2" in r.output
    assert route.called
    # extracted file should be cleaned up by default
    assert not (tmp_path / "in.opus").exists()


@respx.mock
def test_upload_with_keep_extracted(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    video = tmp_path / "in.webm"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1",
         "-c:a", "libopus", str(video)],
        capture_output=True, check=True, timeout=30,
    )
    respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j3", "status": "uploaded"})
    )
    r = CliRunner().invoke(main, ["upload", str(video), "--keep-extracted"])
    assert r.exit_code == 0
    assert (tmp_path / "in.opus").exists()


@respx.mock
def test_upload_fails_on_413(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    audio = tmp_path / "x.opus"; audio.write_bytes(b"x")
    respx.post("https://x/jobs").mock(return_value=httpx.Response(413))
    r = CliRunner().invoke(main, ["upload", str(audio)])
    assert r.exit_code != 0
    assert "exceeds" in r.output or "413" in r.output


@respx.mock
def test_upload_sends_fallback_recorded_at(tmp_path, monkeypatch):
    """Дату записи сервер достаёт из имени; у файлов без даты в имени её заменяет mtime."""
    import os
    from datetime import datetime
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    audio = tmp_path / "video1855516632.opus"
    audio.write_bytes(b"OggS\x00\x00\x00\x00")
    when = datetime(2026, 9, 7, 16, 34).timestamp()
    os.utime(audio, (when, when))
    route = respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j", "status": "uploaded"})
    )
    r = CliRunner().invoke(main, ["upload", str(audio)])
    assert r.exit_code == 0, r.output
    assert b'name="fallback_recorded_at"' in route.calls[0].request.content
    assert b"2026-09-07" in route.calls[0].request.content
