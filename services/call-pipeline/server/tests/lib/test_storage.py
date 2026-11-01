from app.lib.storage import audio_path, transcript_path, draft_path, ensure_user_dirs


def test_audio_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_STORAGE_ROOT", str(tmp_path))
    assert audio_path("u-1", "j-1", "opus") == tmp_path / "audio" / "u-1" / "j-1.opus"


def test_ensure_user_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_STORAGE_ROOT", str(tmp_path))
    ensure_user_dirs("u-1")
    for d in ("audio", "transcripts", "drafts"):
        assert (tmp_path / d / "u-1").is_dir()


def test_transcript_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_STORAGE_ROOT", str(tmp_path))
    assert transcript_path("u-1", "j-1") == tmp_path / "transcripts" / "u-1" / "j-1.json"


def test_draft_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_STORAGE_ROOT", str(tmp_path))
    assert draft_path("u-1", "j-1") == tmp_path / "drafts" / "u-1" / "j-1.json"
