import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.routes import web_auth
    web_auth._attempts.clear()


def _make_user(*, is_admin=False, email=None, password=None):
    from app.db import session as s_mod
    from app.db.models import User
    from app.lib.passwords import hash_password
    db = s_mod.SessionLocal()
    u = User(id=str(uuid.uuid4()), name="T", bearer_token=str(uuid.uuid4()),
             email=email, is_admin=is_admin,
             password_hash=hash_password(password) if password else None)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u


def _admin_cookies(client):
    from app.lib.web_session import COOKIE_NAME, issue_session
    admin = _make_user(is_admin=True, email="a@test.ru")
    return {COOKIE_NAME: issue_session(admin.id)}


def test_non_admin_forbidden(client):
    from app.lib.web_session import COOKIE_NAME, issue_session
    u = _make_user()
    r = client.get("/web/admin/users", cookies={COOKIE_NAME: issue_session(u.id)})
    assert r.status_code == 403


def test_list_users(client):
    cookies = _admin_cookies(client)
    _make_user(email="b@test.ru")
    r = client.get("/web/admin/users", cookies=cookies)
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()["users"]}
    assert {"a@test.ru", "b@test.ru"} <= emails
    for u in r.json()["users"]:
        assert {"id", "name", "email", "is_admin", "is_active",
                "jobs_count", "recordings_count", "created_at"} <= set(u)


def test_create_user_and_login(client):
    cookies = _admin_cookies(client)
    r = client.post("/web/admin/users", cookies=cookies,
                    json={"name": "Новый", "email": "New@Test.RU"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@test.ru"          # нормализован
    assert len(body["temp_password"]) >= 10
    assert body["bearer_token"]
    r2 = client.post("/web/login", json={"email": "new@test.ru",
                                          "password": body["temp_password"]})
    assert r2.status_code == 200


def test_create_user_duplicate_email(client):
    cookies = _admin_cookies(client)
    client.post("/web/admin/users", cookies=cookies,
                json={"name": "A", "email": "dup@test.ru"})
    r = client.post("/web/admin/users", cookies=cookies,
                    json={"name": "B", "email": "dup@test.ru"})
    assert r.status_code == 409


def test_reset_password(client):
    cookies = _admin_cookies(client)
    u = _make_user(email="x@test.ru", password="old-pass-1")
    r = client.post(f"/web/admin/users/{u.id}/reset-password", cookies=cookies)
    assert r.status_code == 200
    temp = r.json()["temp_password"]
    assert client.post("/web/login", json={"email": "x@test.ru",
                                            "password": temp}).status_code == 200


def test_set_active(client):
    cookies = _admin_cookies(client)
    u = _make_user(email="y@test.ru", password="pass-1234")
    r = client.post(f"/web/admin/users/{u.id}/set-active", cookies=cookies,
                    json={"active": False})
    assert r.status_code == 200
    assert client.post("/web/login", json={"email": "y@test.ru",
                                            "password": "pass-1234"}).status_code == 401


def test_set_email(client):
    cookies = _admin_cookies(client)
    u = _make_user()  # Telegram-only юзер без email
    r = client.post(f"/web/admin/users/{u.id}/set-email", cookies=cookies,
                    json={"email": "julia@test.ru"})
    assert r.status_code == 200
    r2 = client.get("/web/admin/users", cookies=cookies)
    assert "julia@test.ru" in {x["email"] for x in r2.json()["users"]}
