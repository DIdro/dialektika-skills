"""Долговечная OAuth-авторизация Mionika для always-on сервиса.

access живёт 24ч, refresh — 30 дней СКОЛЬЗЯЩИХ (продлевается при каждом обмене на
стороне Mionika). Сервис, дёргающий Mionika хотя бы раз в месяц, не теряет авторизацию.
Здесь: при близком истечении access меняем его по refresh-токену и сохраняем обновлённые
креды в тот же файл. stdlib-only.

Файл кредов (OPS_INGEST_MIONIKA_CREDS) — JSON с полями:
  accessToken, refreshToken, expiresAt(ms), clientId, serverUrl
"""
import json
import time
import urllib.parse
import urllib.request

REFRESH_THRESHOLD_MS = 5 * 60 * 1000   # обновляем, если до истечения < 5 мин


def needs_refresh(expires_at_ms, now_ms, threshold_ms=REFRESH_THRESHOLD_MS) -> bool:
    try:
        return (int(expires_at_ms) - int(now_ms)) < threshold_ms
    except (ValueError, TypeError):
        return True   # неизвестно когда истекает → лучше обновить


def token_endpoint(server_url) -> str:
    base = (server_url or "https://mionika.ryefield.com").rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]
    return base + "/oauth/token"


def _refresh(creds: dict) -> dict:
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": creds["refreshToken"],
        "client_id": creds["clientId"],
    }).encode()
    req = urllib.request.Request(token_endpoint(creds.get("serverUrl")), data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    creds["accessToken"] = body["access_token"]
    creds["refreshToken"] = body.get("refresh_token", creds["refreshToken"])  # RFC6749: может ротироваться
    creds["expiresAt"] = int(time.time() * 1000) + int(body.get("expires_in", 86400)) * 1000
    return creds


def token(creds_path: str, now_ms=None) -> str:
    """Вернуть валидный access-токен, обновив его при необходимости и сохранив креды."""
    with open(creds_path, encoding="utf-8") as f:
        creds = json.load(f)
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if needs_refresh(creds.get("expiresAt"), now):
        creds = _refresh(creds)
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(creds, f, ensure_ascii=False, indent=2)
    return creds["accessToken"]
