import pytest
from app.lib.web_session import issue_session, verify_session


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("PIPELINE_WEB_SECRET", "test-secret")
    token = issue_session("user-123")
    assert verify_session(token) == "user-123"


def test_tampered_token(monkeypatch):
    monkeypatch.setenv("PIPELINE_WEB_SECRET", "test-secret")
    token = issue_session("user-123")
    assert verify_session(token[:-2] + "xx") is None


def test_wrong_secret(monkeypatch):
    monkeypatch.setenv("PIPELINE_WEB_SECRET", "secret-a")
    token = issue_session("user-123")
    monkeypatch.setenv("PIPELINE_WEB_SECRET", "secret-b")
    assert verify_session(token) is None


def test_no_secret(monkeypatch):
    monkeypatch.delenv("PIPELINE_WEB_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        issue_session("user-123")
    assert verify_session("whatever") is None
