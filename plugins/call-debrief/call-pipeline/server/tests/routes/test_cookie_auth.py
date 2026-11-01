import uuid


def _make_user(*, is_active=True, is_admin=False, token=None):
    from app.db import session as s_mod
    from app.db.models import User
    db = s_mod.SessionLocal()
    u = User(id=str(uuid.uuid4()), name="T",
             bearer_token=token or str(uuid.uuid4()),
             is_active=is_active, is_admin=is_admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u


def test_cookie_auth_works(client):
    from app.lib.web_session import COOKIE_NAME, issue_session
    u = _make_user()
    r = client.get("/jobs", cookies={COOKIE_NAME: issue_session(u.id)})
    assert r.status_code == 200


def test_bad_cookie_rejected(client):
    from app.lib.web_session import COOKIE_NAME
    _make_user()
    r = client.get("/jobs", cookies={COOKIE_NAME: "garbage"})
    assert r.status_code == 403


def test_bearer_still_works(client):
    u = _make_user()
    r = client.get("/jobs", headers={"Authorization": f"Bearer {u.bearer_token}"})
    assert r.status_code == 200


def test_inactive_user_bearer_rejected(client):
    u = _make_user(is_active=False)
    r = client.get("/jobs", headers={"Authorization": f"Bearer {u.bearer_token}"})
    assert r.status_code == 403


def test_inactive_user_cookie_rejected(client):
    from app.lib.web_session import COOKIE_NAME, issue_session
    u = _make_user(is_active=False)
    r = client.get("/jobs", cookies={COOKIE_NAME: issue_session(u.id)})
    assert r.status_code == 403


def test_no_credentials_401(client):
    r = client.get("/jobs")
    assert r.status_code == 401
