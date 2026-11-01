import os
import pytest
from app.lib.encryption import encrypt, decrypt, generate_key

@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("PIPELINE_DB_ENCRYPTION_KEY", generate_key())

def test_round_trip():
    plain = "hf_abcdef123456"
    cipher = encrypt(plain)
    assert cipher != plain
    assert decrypt(cipher) == plain

def test_unicode_round_trip():
    plain = "пароль с пробелами и emoji 🔐"
    assert decrypt(encrypt(plain)) == plain

def test_decrypt_wrong_key_raises(monkeypatch):
    cipher = encrypt("secret")
    monkeypatch.setenv("PIPELINE_DB_ENCRYPTION_KEY", generate_key())
    with pytest.raises(Exception):
        decrypt(cipher)

def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("PIPELINE_DB_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        encrypt("x")
