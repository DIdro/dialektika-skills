import httpx
import respx
from click.testing import CliRunner
from pipeline_cli.cli import main


def _config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('''
[server]
url = "https://x"
bearer_token = "t"
user_id = "u"
''')
    return cfg


@respx.mock
def test_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    respx.get("https://x/jobs").mock(
        return_value=httpx.Response(200, json={
            "jobs": [{"job_id": "abcd1234efgh5678", "status": "drafted",
                      "mode": "call",
                      "created_at": "2026-05-10T12:00:00"}]
        })
    )
    r = CliRunner().invoke(main, ["list"])
    assert r.exit_code == 0
    assert "drafted" in r.output


@respx.mock
def test_list_with_status_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    respx.get("https://x/jobs", params={"status": "uploaded"}).mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    r = CliRunner().invoke(main, ["list", "--status", "uploaded"])
    assert r.exit_code == 0


@respx.mock
def test_show_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    respx.get("https://x/jobs/j1").mock(
        return_value=httpx.Response(200, json={"job_id": "j1", "status": "drafted",
                                                "mode": "call"})
    )
    r = CliRunner().invoke(main, ["show", "j1"])
    assert r.exit_code == 0
    assert "drafted" in r.output


@respx.mock
def test_show_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    respx.get("https://x/jobs/j1/transcript").mock(
        return_value=httpx.Response(200, json={"text": "hello world",
                                                "segments": []})
    )
    r = CliRunner().invoke(main, ["show", "j1", "--transcript"])
    assert r.exit_code == 0
    assert "hello world" in r.output


@respx.mock
def test_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    route = respx.delete("https://x/jobs/j1").mock(
        return_value=httpx.Response(200, json={"deleted": True})
    )
    r = CliRunner().invoke(main, ["skip", "j1"])
    assert r.exit_code == 0
    assert "j1" in r.output
    assert route.called


@respx.mock
def test_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    respx.get("https://x/health").mock(
        return_value=httpx.Response(200, json={"ok": True, "disk_warning": False,
                                                "disk_free_gb": 50.0})
    )
    # Two list_jobs calls in status (drafted + transcribed + uploaded). Mock all.
    respx.get("https://x/jobs", params={"status": "drafted"}).mock(
        return_value=httpx.Response(200, json={"jobs": [{"job_id": "a"}]})
    )
    respx.get("https://x/jobs", params={"status": "transcribed"}).mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    respx.get("https://x/jobs", params={"status": "uploaded"}).mock(
        return_value=httpx.Response(200, json={"jobs": [{"job_id": "b"}]})
    )
    r = CliRunner().invoke(main, ["status"])
    assert r.exit_code == 0
    assert "Pending jobs: 2" in r.output


@respx.mock
def test_status_disk_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_CLI_CONFIG_DIR", str(tmp_path))
    _config(tmp_path)
    respx.get("https://x/health").mock(
        return_value=httpx.Response(200, json={"ok": True, "disk_warning": True,
                                                "disk_free_gb": 3.2})
    )
    respx.get("https://x/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    r = CliRunner().invoke(main, ["status"])
    assert r.exit_code == 0
    assert "3.2" in r.output  # disk warning shows free GB
