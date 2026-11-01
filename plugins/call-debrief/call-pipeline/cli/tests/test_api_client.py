import httpx
import pytest
import respx
from pipeline_cli.api_client import APIClient, APIError


def _client():
    return APIClient(base_url="https://x", token="t")


@respx.mock
def test_get_jobs():
    respx.get("https://x/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"job_id": "j"}]})
    )
    assert _client().list_jobs() == [{"job_id": "j"}]


@respx.mock
def test_skip_job():
    route = respx.delete("https://x/jobs/j").mock(
        return_value=httpx.Response(200, json={"deleted": True})
    )
    _client().delete_job("j")
    assert route.called


@respx.mock
def test_get_job_404():
    respx.get("https://x/jobs/j").mock(return_value=httpx.Response(404))
    with pytest.raises(LookupError):
        _client().get_job("j")


@respx.mock
def test_get_transcript_returns_dict():
    respx.get("https://x/jobs/j/transcript").mock(
        return_value=httpx.Response(200, json={"text": "hello", "segments": []})
    )
    out = _client().get_transcript("j")
    assert out["text"] == "hello"


@respx.mock
def test_upload_413_raises_apierror(tmp_path):
    respx.post("https://x/jobs").mock(return_value=httpx.Response(413))
    f = tmp_path / "x.opus"; f.write_bytes(b"X")
    with pytest.raises(APIError, match="exceeds"):
        _client().upload(f, mode="call", user_id="u")


@respx.mock
def test_health():
    respx.get("https://x/health").mock(
        return_value=httpx.Response(200, json={"ok": True, "disk_warning": False})
    )
    h = _client().health()
    assert h["ok"] is True


@respx.mock
def test_admin_disk_status():
    respx.get("https://x/admin/disk-status").mock(
        return_value=httpx.Response(200, json={"free_gb": 10.5, "warning": False})
    )
    client = APIClient(base_url="https://x", token="t", admin_token="ADMIN")
    out = client.admin_disk_status()
    assert out["free_gb"] == 10.5


@respx.mock
def test_list_jobs_with_status_filter():
    respx.get("https://x/jobs", params={"status": "drafted"}).mock(
        return_value=httpx.Response(200, json={"jobs": [{"job_id": "j", "status": "drafted"}]})
    )
    rows = _client().list_jobs(status="drafted")
    assert rows[0]["status"] == "drafted"
