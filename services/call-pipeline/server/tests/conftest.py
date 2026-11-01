import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.lib.encryption import generate_key


# At collection time, source HF_TOKEN (and any other secrets) from
# the user's existing ops/.env so pyannote-driven tests can run.
# This is read-only and does not overwrite already-set env vars.
def _load_ops_env() -> None:
    ops_env = Path(__file__).resolve().parents[3] / "ops" / ".env"
    if not ops_env.is_file():
        return
    for line in ops_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_ops_env()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("PIPELINE_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("PIPELINE_DB_ENCRYPTION_KEY", generate_key())
    monkeypatch.setenv("PIPELINE_WEB_SECRET", "test-web-secret")


@pytest.fixture
def client():
    # Force reimport so engine picks up env-var
    from importlib import reload
    from app.db import session as s_mod
    reload(s_mod)
    from app import main as m_mod
    reload(m_mod)
    with TestClient(m_mod.app) as c:
        yield c
