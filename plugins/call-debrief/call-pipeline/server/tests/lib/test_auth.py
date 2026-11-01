import pytest
from fastapi import HTTPException
from app.lib.auth import verify_bearer

class FakeReq:
    def __init__(self, header): self.headers = {"authorization": header} if header else {}

def test_missing_header_raises_401():
    with pytest.raises(HTTPException) as e:
        verify_bearer(None)
    assert e.value.status_code == 401

def test_malformed_raises_401():
    with pytest.raises(HTTPException) as e:
        verify_bearer("not bearer something")
    assert e.value.status_code == 401

def test_valid_returns_token():
    assert verify_bearer("Bearer abc123") == "abc123"
