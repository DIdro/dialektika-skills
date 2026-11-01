import json

from ops_ingest.mionika_auth import needs_refresh, token_endpoint, token


def test_needs_refresh():
    now = 1_000_000_000_000
    assert needs_refresh(now + 60_000, now) is True          # истекает через 1 мин (<5) → да
    assert needs_refresh(now + 3_600_000, now) is False       # через час → нет
    assert needs_refresh(None, now) is True                   # неизвестно → да
    assert needs_refresh("мусор", now) is True


def test_token_endpoint_strips_mcp():
    assert token_endpoint("https://mionika.ryefield.com/mcp") == "https://mionika.ryefield.com/oauth/token"
    assert token_endpoint("https://mionika.ryefield.com") == "https://mionika.ryefield.com/oauth/token"
    assert token_endpoint(None) == "https://mionika.ryefield.com/oauth/token"


def test_token_no_refresh_when_fresh(tmp_path):
    # access ещё долго живёт → token() возвращает его без сети
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"accessToken": "AAA", "refreshToken": "R", "clientId": "C",
                             "serverUrl": "https://mionika.ryefield.com/mcp",
                             "expiresAt": 9_999_999_999_999}), encoding="utf-8")
    assert token(str(p)) == "AAA"
