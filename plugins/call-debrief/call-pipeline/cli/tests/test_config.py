import pytest
import tomli_w
from pipeline_cli.config import load_config, ConfigMissing, config_path


def test_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConfigMissing):
        load_config()


def test_load_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(tomli_w.dumps({
        "server": {"url": "https://x", "bearer_token": "t", "user_id": "u"},
    }).encode())
    c = load_config()
    assert c.server_url == "https://x"
    assert c.bearer_token == "t"
    assert c.user_id == "u"


def test_admin_token_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(tomli_w.dumps({
        "server": {"url": "https://x", "bearer_token": "t",
                   "user_id": "u", "admin_token": "ADMIN"},
    }).encode())
    c = load_config()
    assert c.admin_token == "ADMIN"
