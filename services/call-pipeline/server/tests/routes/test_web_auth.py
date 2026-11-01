import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.routes import web_auth
    web_auth._attempts.clear()


def _make_user(email="u@test.ru", password="pass-123", **kw):
    from app.db import session as s_mod
    from app.db.models import User
    from app.lib.passwords import hash_password
    db = s_mod.SessionLocal()
    u = User(id=str(uuid.uuid4()), name="T", bearer_token=str(uuid.uuid4()),
             email=email, password_hash=hash_password(password), **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u


def _login(client, email="u@test.ru", password="pass-123"):
    return client.post("/web/login", json={"email": email, "password": password})


def _session_cookie(resp):
    from app.lib.web_session import COOKIE_NAME
    return {COOKIE_NAME: resp.cookies[COOKIE_NAME]}


def test_login_ok(client):
    u = _make_user()
    r = _login(client)
    assert r.status_code == 200
    assert r.json()["id"] == u.id
    assert "cp_session" in r.cookies


def test_login_wrong_password(client):
    _make_user()
    r = _login(client, password="wrong")
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = _login(client, email="nobody@test.ru")
    assert r.status_code == 401


def test_login_rate_limit(client):
    _make_user()
    for _ in range(5):
        _login(client, password="wrong")
    r = _login(client)
    assert r.status_code == 429


def test_me(client):
    u = _make_user()
    cookies = _session_cookie(_login(client))
    r = client.get("/web/me", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"id": u.id, "name": "T", "email": "u@test.ru", "is_admin": False}


def test_logout_clears_cookie(client):
    _make_user()
    cookies = _session_cookie(_login(client))
    r = client.post("/web/logout", cookies=cookies)
    assert r.status_code == 200
    assert 'cp_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]


def test_change_password(client):
    _make_user()
    cookies = _session_cookie(_login(client))
    r = client.post("/web/change-password", cookies=cookies,
                    json={"old_password": "pass-123", "new_password": "new-456!!"})
    assert r.status_code == 200
    assert _login(client, password="new-456!!").status_code == 200
    # старый пароль больше не подходит (может попасть в rate-limit окно — 401 или 429)
    assert _login(client, password="pass-123").status_code in (401, 429)


def test_change_password_wrong_old(client):
    _make_user()
    cookies = _session_cookie(_login(client))
    r = client.post("/web/change-password", cookies=cookies,
                    json={"old_password": "wrong", "new_password": "new-456!!"})
    assert r.status_code == 403
