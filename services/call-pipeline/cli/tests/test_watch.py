import time
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from pipeline_cli.cli import main
from pipeline_cli.commands.watch import _is_stable, _scan


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[server]\nurl = "https://x"\nbearer_token = "t"\nuser_id = "u"\n',
        encoding="utf-8",
    )
    return cfg


def test_is_stable_fresh_file_is_unstable(tmp_path):
    f = tmp_path / "live.wav"
    f.write_bytes(b"x")
    assert not _is_stable(f, min_age_sec=60)


def test_is_stable_old_file_is_stable(tmp_path):
    f = tmp_path / "old.wav"
    f.write_bytes(b"x")
    # Backdate the mtime by 5 minutes
    five_min_ago = time.time() - 300
    import os
    os.utime(f, (five_min_ago, five_min_ago))
    assert _is_stable(f, min_age_sec=60)


def test_scan_filters_to_audio_video_extensions(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "b.webm").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("nope")
    (tmp_path / "image.png").write_bytes(b"x")
    found = sorted(p.name for p in _scan(tmp_path))
    assert found == ["a.wav", "b.webm"]


def test_scan_missing_folder_returns_empty(tmp_path):
    assert _scan(tmp_path / "nonexistent") == []


@respx.mock
def test_watch_once_uploads_new_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "config.toml").write_text(
        '[server]\nurl = "https://x"\nbearer_token = "t"\nuser_id = "u"\n',
        encoding="utf-8",
    )
    # state file lands in user_state_dir — point it inside tmp via env override
    # is awkward (platformdirs reads no env for state). Easiest: monkeypatch
    # _state_path() to a tmp file.
    import pipeline_cli.commands.watch as watch_mod
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(watch_mod, "_state_path", lambda: state_file)

    folder = tmp_path / "watched"; folder.mkdir()
    src = folder / "call.opus"
    src.write_bytes(b"OggS" + b"\x00" * 1000)
    # backdate so it passes min_age
    import os
    five_min_ago = time.time() - 300
    os.utime(src, (five_min_ago, five_min_ago))

    route = respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j-1", "status": "uploaded"})
    )
    r = CliRunner().invoke(main, ["watch", str(folder), "--once", "--min-age", "60"])
    assert r.exit_code == 0, r.output
    assert route.called
    # state file should have an entry for the SHA, recording the job_id
    import json
    state = json.loads(state_file.read_text(encoding="utf-8"))
    job_ids = [v.get("job_id") for v in state.values()
               if isinstance(v, dict) and "job_id" in v]
    assert "j-1" in job_ids


@respx.mock
def test_watch_once_skips_already_uploaded(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "config.toml").write_text(
        '[server]\nurl = "https://x"\nbearer_token = "t"\nuser_id = "u"\n',
        encoding="utf-8",
    )
    import pipeline_cli.commands.watch as watch_mod
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(watch_mod, "_state_path", lambda: state_file)

    folder = tmp_path / "watched"; folder.mkdir()
    src = folder / "x.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 100)
    import os
    os.utime(src, (time.time() - 300, time.time() - 300))

    # Single route used across both runs — assert call_count doesn't grow on the
    # second invocation.
    route = respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j-A", "status": "uploaded"})
    )
    r1 = CliRunner().invoke(main, ["watch", str(folder), "--once", "--min-age", "60"])
    assert r1.exit_code == 0, r1.output
    assert route.call_count == 1

    r2 = CliRunner().invoke(main, ["watch", str(folder), "--once", "--min-age", "60"])
    assert r2.exit_code == 0, r2.output
    # The second run must skip — no new POST.
    assert route.call_count == 1, (
        f"Expected no second POST, got {route.call_count} calls. Output: {r2.output}"
    )


def test_is_short_video_only_for_generated_containers(monkeypatch):
    import pipeline_cli.commands.watch as watch_mod
    monkeypatch.setattr(watch_mod, "probe_duration", lambda p: 107.0)
    assert watch_mod.is_short_video(Path("Умка и Лиза.mp4"))
    assert watch_mod.is_short_video(Path("clip.MOV"))
    # .webm — запись встречи Телемоста, бывает и короткой: не отсекаем
    assert not watch_mod.is_short_video(Path("Запись встречи.webm"))
    assert not watch_mod.is_short_video(Path("voice.opus"))


def test_is_short_video_keeps_long_and_unprobeable(monkeypatch):
    import pipeline_cli.commands.watch as watch_mod
    monkeypatch.setattr(watch_mod, "probe_duration", lambda p: 3600.0)
    assert not watch_mod.is_short_video(Path("Запись ИСКРА.mp4"))
    monkeypatch.setattr(watch_mod, "probe_duration", lambda p: None)
    assert not watch_mod.is_short_video(Path("broken.mp4"))


@respx.mock
def test_watch_once_skips_short_video(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "config.toml").write_text(
        '[server]\nurl = "https://x"\nbearer_token = "t"\nuser_id = "u"\n',
        encoding="utf-8",
    )
    import pipeline_cli.commands.watch as watch_mod
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(watch_mod, "_state_path", lambda: state_file)
    monkeypatch.setattr(watch_mod, "probe_duration", lambda p: 107.0)

    folder = tmp_path / "watched"; folder.mkdir()
    src = folder / "мультик.mp4"
    src.write_bytes(b"\x00" * 100)
    import os
    os.utime(src, (time.time() - 300, time.time() - 300))

    route = respx.post("https://x/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "j-V", "status": "uploaded"})
    )
    r = CliRunner().invoke(main, ["watch", str(folder), "--once", "--min-age", "60"])
    assert r.exit_code == 0, r.output
    assert not route.called
    assert "short video" in r.output
