import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from ops_ingest import api as api_mod
from ops_ingest.api import create_app
from ops_ingest.store import Store


class _Cfg:
    cp_base = "http://cp"
    owner_user_id = "u-owner"
    mionika_token = "m"
    anthropic_key = "k"
    cp_token = "t"


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "s.db"))
    store.upsert_queue_row("j1", source="call:j1", summary="итоги", count=2,
                           project="demo-bot", confidence=0.9, origin="zoom",
                           atoms=[{"kind": "note", "text": "а"}], status="awaiting")
    monkeypatch.setattr("ops_ingest.api.fetch_me",
                        lambda base, cookie: {"id": "u-owner"} if cookie else None)
    return TestClient(create_app(_Cfg(), store)), store


def test_reviews_requires_session(app_client):
    client, _ = app_client
    assert client.get("/reviews").status_code == 401


def test_reviews_lists_queue(app_client):
    client, _ = app_client
    client.cookies.set("cp_session", "s")
    body = client.get("/reviews").json()
    assert [r["job_id"] for r in body["reviews"]] == ["j1"]
    assert body["reviews"][0]["origin"] == "zoom"


def test_retarget_changes_project(app_client):
    client, store = app_client
    client.cookies.set("cp_session", "s")
    r = client.post("/reviews/j1/retarget", json={"project_key": "aaa-demo"})
    assert r.status_code == 200
    assert store.get_queue_row("j1")["project"] == "aaa-demo"


def test_accept_returns_confirm_on_contradiction(app_client, monkeypatch):
    client, _ = app_client
    client.cookies.set("cp_session", "s")

    async def fake_accept(cfg, store, job_id, *, force=False):
        return {"needs_confirm": True, "contradictions": [{"index": 0, "note": "спорит"}]}

    monkeypatch.setattr("ops_ingest.api.ra.accept", fake_accept)
    body = client.post("/reviews/j1/accept", json={}).json()
    assert body["needs_confirm"] is True


def test_unknown_job_gives_404(app_client):
    client, _ = app_client
    client.cookies.set("cp_session", "s")
    assert client.get("/reviews/nope").status_code == 404


# --- пути отказа: чужой пользователь, невалидная сессия, недоступный сервис проверки ---


def test_foreign_user_gets_403_and_no_queue_leaks(app_client, monkeypatch):
    """Сессия валидна (сервис проверки её подтверждает), но принадлежит не владельцу.
    Это должно быть 403, а не 401 — сессия НЕ отвергнута, отвергнут человек за ней,
    и ни строка очереди, ни список проектов наружу уйти не должны."""
    client, _ = app_client
    monkeypatch.setattr("ops_ingest.api.fetch_me",
                        lambda base, cookie: {"id": "u-someone-else"} if cookie else None)
    client.cookies.set("cp_session", "valid-but-not-owner")

    r = client.get("/reviews")
    assert r.status_code == 403
    assert "reviews" not in r.json()
    assert "j1" not in r.text

    r2 = client.get("/reviews/j1")
    assert r2.status_code == 403
    assert "summary" not in r2.json()

    def _must_not_be_called(*_a, **_kw):
        raise AssertionError("mio.fetch_projects не должен вызываться для чужого пользователя")

    monkeypatch.setattr("ops_ingest.mionika_client.fetch_projects", _must_not_be_called)
    r3 = client.get("/projects")
    assert r3.status_code == 403
    assert "projects" not in r3.json()


def test_rejected_cookie_gives_401_distinct_from_missing_cookie(app_client, monkeypatch):
    """cookie передан, но сервис проверки её не подтвердил (сессия истекла/подделана) —
    это отдельный от полного отсутствия cookie случай (см. test_reviews_requires_session),
    и оба дают 401, а не 403: личность вообще не установлена, «чужого пользователя» здесь
    ещё нет."""
    client, _ = app_client
    monkeypatch.setattr("ops_ingest.api.fetch_me", lambda base, cookie: None)
    client.cookies.set("cp_session", "expired-or-forged")

    r = client.get("/reviews")
    assert r.status_code == 401
    assert "reviews" not in r.json()


class _FakeResponse:
    """Заглушка httpx.Response для юнит-тестов fetch_me: код ответа + либо готовое
    JSON-значение, либо исключение, которое должен бросить .json()."""

    def __init__(self, status_code=200, json_value=None, json_error=None):
        self.status_code = status_code
        self._json_value = json_value
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_value


class _FakeHttpxClient:
    """Заглушка httpx.Client(...) — контекстный менеджер с единственным .get(),
    отдающим заготовленный ответ либо бросающим сетевую ошибку."""

    def __init__(self, response=None, raise_error=None, **_kw):
        self._response = response
        self._raise_error = raise_error

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, *_a, **_kw):
        if self._raise_error is not None:
            raise self._raise_error
        return self._response


def test_check_service_unavailable_gives_401_without_leaking_queue(tmp_path, monkeypatch):
    """Сетевая ошибка при обращении к /web/me (сервис проверки лёг) — не должна долетать
    как 500 и не должна открывать очередь: fetch_me гасит её сама, require_owner видит None
    и отвечает обычным 401, как при любой неподтверждённой сессии."""
    store = Store(str(tmp_path / "s2.db"))
    store.upsert_queue_row("j1", source="call:j1", summary="итоги", count=1,
                           project="demo-bot", confidence=0.9, origin="zoom",
                           atoms=[], status="awaiting")
    monkeypatch.setattr(api_mod.httpx, "Client",
                        lambda **kw: _FakeHttpxClient(
                            raise_error=httpx.ConnectError("cp-сервис недоступен")))

    client = TestClient(create_app(_Cfg(), store))
    client.cookies.set("cp_session", "s")
    r = client.get("/reviews")
    assert r.status_code == 401
    assert "reviews" not in r.json()
    assert "j1" not in r.text


def test_fetch_me_none_without_cookie():
    assert api_mod.fetch_me("http://cp", None) is None


def test_fetch_me_none_on_non_200_status(monkeypatch):
    monkeypatch.setattr(api_mod.httpx, "Client",
                        lambda **kw: _FakeHttpxClient(response=_FakeResponse(status_code=401)))
    assert api_mod.fetch_me("http://cp", "s") is None


def test_fetch_me_none_on_network_error(monkeypatch):
    monkeypatch.setattr(api_mod.httpx, "Client",
                        lambda **kw: _FakeHttpxClient(
                            raise_error=httpx.ConnectError("cp-сервис недоступен")))
    assert api_mod.fetch_me("http://cp", "s") is None


def test_fetch_me_none_on_non_json_body(monkeypatch):
    """Проверяющий сервис ответил 200, но телом не-JSON — разбор не должен ронять
    процесс необработанным исключением, это тоже просто отказ."""
    monkeypatch.setattr(api_mod.httpx, "Client",
                        lambda **kw: _FakeHttpxClient(response=_FakeResponse(
                            status_code=200, json_error=ValueError("не JSON"))))
    assert api_mod.fetch_me("http://cp", "s") is None


def test_projects_refreshes_mionika_token(app_client, monkeypatch):
    """API обязан обновлять токен Мионики перед обращением к ней.

    Access-токен живёт сутки. Поллер обновляет его каждым циклом, а процесс API
    читает конфиг один раз при старте — и ровно через сутки Мионика начинает
    отвечать 401. Так уже было: : выпадашка проектов опустела, причём молча.
    """
    client, _ = app_client
    client.cookies.set("cp_session", "s")
    calls = []
    monkeypatch.setattr("ops_ingest.api.refresh_mionika", lambda cfg: calls.append(1))

    async def fake_fetch(tok):
        return [{"key": "iskra", "name": "Искра"}]

    monkeypatch.setattr("ops_ingest.mionika_client.fetch_projects", fake_fetch)
    r = client.get("/projects")
    assert r.status_code == 200
    assert calls, "токен не обновлялся перед обращением к Мионике"


def _anthropic_request():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("path, body", [
    ("/reviews/j1/accept", {}),
    ("/reviews/j1/split", {"prompt": "разнеси"}),
    ("/reviews/j1/reparse", {"prompt": "переразбери"}),
])
def test_model_out_of_credits_gives_readable_503(app_client, monkeypatch, path, body):
    """Так уже было: : кредиты Anthropic кончились, «Принять» и «Разнести» падали
    голым 500 с телом-текстом — а в HTTP/2 у ответа нет statusText, и дашборд
    показывал «Не удалось выполнить действие:» с пустотой после двоеточия.
    Причина должна доехать до кнопки словами."""
    client, store = app_client
    client.cookies.set("cp_session", "s")
    monkeypatch.setattr("ops_ingest.api.refresh_mionika", lambda cfg: None)

    async def out_of_credits(*_a, **_kw):
        raise anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.",
            response=httpx.Response(400, request=_anthropic_request()),
            body={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API."}})

    for name in ("accept", "split", "reparse"):
        monkeypatch.setattr(f"ops_ingest.api.ra.{name}", out_of_credits)
    r = client.post(path, json=body)
    assert r.status_code == 503
    assert "кредиты" in r.json()["detail"]
    assert store.get_queue_row("j1")["status"] == "awaiting"


def test_model_unreachable_gives_readable_503(app_client, monkeypatch):
    """Любая другая ошибка модели (сеть, перегрузка) — тоже словами, а не пустотой."""
    client, _ = app_client
    client.cookies.set("cp_session", "s")
    monkeypatch.setattr("ops_ingest.api.refresh_mionika", lambda cfg: None)

    async def unreachable(*_a, **_kw):
        raise anthropic.APIConnectionError(request=_anthropic_request())

    monkeypatch.setattr("ops_ingest.api.ra.accept", unreachable)
    r = client.post("/reviews/j1/accept", json={})
    assert r.status_code == 503
    assert "Модель недоступна" in r.json()["detail"]


def test_accept_refreshes_mionika_token(app_client, monkeypatch):
    """То же для приёмки: она пишет в граф и падала бы 401 на протухшем токене."""
    client, _ = app_client
    client.cookies.set("cp_session", "s")
    calls = []
    monkeypatch.setattr("ops_ingest.api.refresh_mionika", lambda cfg: calls.append(1))

    async def fake_accept(cfg, store, job_id, *, force=False):
        return {"written": 1}

    monkeypatch.setattr("ops_ingest.api.ra.accept", fake_accept)
    assert client.post("/reviews/j1/accept", json={}).status_code == 200
    assert calls, "токен не обновлялся перед записью в граф"
