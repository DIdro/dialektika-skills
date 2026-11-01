import pytest


@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setenv("PIPELINE_ADMIN_TOKEN", "ADMIN")


def test_add_user_requires_admin(client):
    r = client.post("/admin/users", json={"name": "Irina"},
                    headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_add_user_creates(client):
    r = client.post("/admin/users",
                    json={"name": "Irina", "context_summary": "hi"},
                    headers={"X-Admin-Token": "ADMIN"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Irina"
    assert body["bearer_token"]
    assert body["id"]


def test_rotate_token(client):
    r = client.post("/admin/users", json={"name": "A"},
                    headers={"X-Admin-Token": "ADMIN"})
    uid = r.json()["id"]
    old = r.json()["bearer_token"]
    r2 = client.post(f"/admin/users/{uid}/rotate-token",
                     headers={"X-Admin-Token": "ADMIN"})
    assert r2.status_code == 200
    assert r2.json()["bearer_token"] != old


def test_disk_status(client):
    r = client.get("/admin/disk-status", headers={"X-Admin-Token": "ADMIN"})
    assert r.status_code == 200
    assert "free_gb" in r.json()


def test_admin_token_missing_in_env_returns_401(client, monkeypatch):
    monkeypatch.delenv("PIPELINE_ADMIN_TOKEN", raising=False)
    r = client.post("/admin/users", json={"name": "X"},
                    headers={"X-Admin-Token": "ADMIN"})
    assert r.status_code == 401
