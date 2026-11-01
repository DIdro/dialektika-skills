import os
from cryptography.fernet import Fernet

_ENV = "PIPELINE_DB_ENCRYPTION_KEY"

def generate_key() -> str:
    return Fernet.generate_key().decode()

def _fernet() -> Fernet:
    key = os.environ.get(_ENV)
    if not key:
        raise RuntimeError(f"{_ENV} env var is required")
    return Fernet(key.encode())

def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
