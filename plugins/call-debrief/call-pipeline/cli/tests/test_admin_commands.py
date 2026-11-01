import httpx
import respx
from click.testing import CliRunner
from pipeline_cli.cli import main


def _config_with_admin(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('''
[server]
url = "https://x"
bearer_token = "t"
user_id = "u"
admin_token = "ADMIN"
''')
    return cfg


def _config_no_admin(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('''
[server]
url = "https://x"
bearer_token = "t"
user_id = "u"
''')
    return cfg


@respx.mock
def test_admin_add_user(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config_with_admin(tmp_path)
    respx.post("https://x/admin/users").mock(
        return_value=httpx.Response(201, json={"id": "u2", "name": "O", "bearer_token": "t2"})
    )
    r = CliRunner().invoke(main, ["admin", "add-user", "--name", "O"])
    assert r.exit_code == 0, r.output
    assert "t2" in r.output


@respx.mock
def test_admin_add_user_with_options(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config_with_admin(tmp_path)
    route = respx.post("https://x/admin/users").mock(
        return_value=httpx.Response(201, json={"id": "u3", "name": "Irina",
                                                "bearer_token": "t3"})
    )
    r = CliRunner().invoke(main, [
        "admin", "add-user", "--name", "Irina",
        "--mionika-token", "mio-secret",
        "--projects", "bot,crm",
        "--context", "Irina leads the studio projects",
    ])
    assert r.exit_code == 0
    assert route.called
    body = route.calls.last.request.read().decode()
    # JSON body should contain the projects list and context
    assert "bot" in body
    assert "Irina leads the studio projects" in body


def test_admin_add_user_no_token_in_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config_no_admin(tmp_path)
    r = CliRunner().invoke(main, ["admin", "add-user", "--name", "X"])
    assert r.exit_code != 0
    assert "admin_token" in r.output.lower() or "missing" in r.output.lower()


@respx.mock
def test_admin_rotate_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config_with_admin(tmp_path)
    respx.post("https://x/admin/users/uid/rotate-token").mock(
        return_value=httpx.Response(200, json={"bearer_token": "rotated-token"})
    )
    r = CliRunner().invoke(main, ["admin", "rotate-token", "uid"])
    assert r.exit_code == 0
    assert "rotated-token" in r.output


@respx.mock
def test_admin_disk_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config_with_admin(tmp_path)
    respx.get("https://x/admin/disk-status").mock(
        return_value=httpx.Response(200, json={"free_gb": 42.0, "warning": False,
                                                "usage_pct": 30.0,
                                                "snapshot": "live"})
    )
    r = CliRunner().invoke(main, ["admin", "disk-status"])
    assert r.exit_code == 0
    assert "42" in r.output


@respx.mock
def test_admin_tail_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config_with_admin(tmp_path)
    respx.get("https://x/admin/jobs/jid/events").mock(
        return_value=httpx.Response(200, json={
            "events": [{"event": "started", "stage": "whisper",
                        "duration_ms": None, "error": None,
                        "at": "2026-05-10T12:00:00"}]
        })
    )
    r = CliRunner().invoke(main, ["admin", "tail-logs", "jid"])
    assert r.exit_code == 0
    assert "started" in r.output
