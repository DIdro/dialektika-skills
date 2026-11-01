def test_health_default(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "disk_warning": False, "disk_free_gb": None}
