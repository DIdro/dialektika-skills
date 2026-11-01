from pathlib import Path

from pipeline_cli.commands.watch import origin_for, prefer_audio


def test_origin_zoom():
    assert origin_for(Path.home() / "Documents" / "Zoom" / "2026-09-01" / "a.m4a") == "zoom"


def test_origin_telegram_desktop():
    assert origin_for(Path.home() / "Downloads" / "Telegram Desktop" / "video1.mp4") == "telegram-desktop"


def test_origin_downloads():
    assert origin_for(Path.home() / "Downloads" / "a.webm") == "downloads"


def test_origin_unknown_folder():
    assert origin_for(Path("/tmp/elsewhere/a.wav")) == "unknown"


def test_prefer_audio_drops_video_twin(tmp_path):
    (tmp_path / "meet.mp4").write_bytes(b"v")
    (tmp_path / "meet.m4a").write_bytes(b"a")
    kept = {p.name for p in prefer_audio([tmp_path / "meet.mp4", tmp_path / "meet.m4a"])}
    assert kept == {"meet.m4a"}


def test_prefer_audio_drops_zoom_prefixed_video_twin(tmp_path):
    """Zoom кладёт пару как audio<id>.m4a + video<id>.mp4 — stem'ы различаются
    префиксом (27.10.2026 встречу с Саватеевым прогнали через whisper дважды)."""
    meet = tmp_path / "2026-10-27 14.51.40 Zoom Meeting"
    meet.mkdir()
    a, v = meet / "audio1728088632.m4a", meet / "video1728088632.mp4"
    kept = {p.name for p in prefer_audio([a, v])}
    assert kept == {"audio1728088632.m4a"}


def test_prefer_audio_keeps_videos_of_other_meetings(tmp_path):
    a = tmp_path / "m1" / "audio111.m4a"
    v = tmp_path / "m2" / "video222.mp4"
    assert {p.name for p in prefer_audio([a, v])} == {"audio111.m4a", "video222.mp4"}


def test_prefer_audio_keeps_lone_video(tmp_path):
    (tmp_path / "solo.mp4").write_bytes(b"v")
    kept = {p.name for p in prefer_audio([tmp_path / "solo.mp4"])}
    assert kept == {"solo.mp4"}


def test_upload_command_passes_origin_derived_from_source_path(tmp_path, monkeypatch):
    """Ручная загрузка тоже проставляет происхождение.

    Без него запись приезжает как «неизвестно», а гейт разбора считает это
    признаком присланного чужого материала — стоп вставал бы на каждой
    загрузке владельца.
    """
    import click.testing
    from pipeline_cli.commands import upload as upload_mod

    src = tmp_path / "Zoom" / "call.m4a"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"RIFFdummy")

    captured = {}

    class _FakeClient:
        def __init__(self, **kw):
            pass

        def upload(self, target, **kw):
            captured.update(kw)
            return {"job_id": "j1", "status": "uploaded"}

    monkeypatch.setattr(upload_mod, "APIClient", _FakeClient)
    monkeypatch.setattr(upload_mod, "load_config", lambda: type(
        "C", (), {"server_url": "https://x", "bearer_token": "t", "user_id": "u"})())

    res = click.testing.CliRunner().invoke(upload_mod.upload, [str(src)])
    assert res.exit_code == 0, res.output
    assert captured["origin"] == "zoom"


def test_upload_origin_can_be_overridden(tmp_path, monkeypatch):
    import click.testing
    from pipeline_cli.commands import upload as upload_mod

    src = tmp_path / "somewhere" / "call.m4a"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"RIFFdummy")
    captured = {}

    class _FakeClient:
        def __init__(self, **kw):
            pass

        def upload(self, target, **kw):
            captured.update(kw)
            return {"job_id": "j1", "status": "uploaded"}

    monkeypatch.setattr(upload_mod, "APIClient", _FakeClient)
    monkeypatch.setattr(upload_mod, "load_config", lambda: type(
        "C", (), {"server_url": "https://x", "bearer_token": "t", "user_id": "u"})())

    res = click.testing.CliRunner().invoke(upload_mod.upload, [str(src), "--origin", "telemost"])
    assert res.exit_code == 0, res.output
    assert captured["origin"] == "telemost"


def test_recorded_date_of_uses_mtime(tmp_path):
    """Дата берётся из mtime файла — у zoom-выгрузок в имени её нет вовсе."""
    import os
    from datetime import datetime
    from pipeline_cli.commands.watch import recorded_date_of
    f = tmp_path / "video1855516632.opus"
    f.write_bytes(b"x")
    when = datetime(2026, 9, 7, 16, 34).timestamp()
    os.utime(f, (when, when))
    assert recorded_date_of(f) == "2026-09-07"
