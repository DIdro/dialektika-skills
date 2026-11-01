from app.lib.filename_date import parse_recorded_at


def test_telemost_recorder_name():
    assert parse_recorded_at("telemost_20260619_143000_1234.ogg") == "2026-06-19"


def test_iso_date_in_name():
    assert parse_recorded_at("zoom_call_2026-06-19.webm") == "2026-06-19"
    assert parse_recorded_at("Запись 2026-06-19 14-30.opus") == "2026-06-19"


def test_bare_yyyymmdd():
    assert parse_recorded_at("20260619_meeting.m4a") == "2026-06-19"


def test_none_when_no_date():
    assert parse_recorded_at("recording.ogg") is None
    assert parse_recorded_at("") is None
    assert parse_recorded_at(None) is None


def test_invalid_date_rejected():
    # 2026-13-40 невалидно → не матчим как дату; нет другой → None
    assert parse_recorded_at("file_2026-13-40.ogg") is None
