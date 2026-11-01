"""Подписанные cookie-сессии для веба (itsdangerous, TTL 30 дней)."""
import os
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "cp_session"
MAX_AGE_SEC = 30 * 24 * 3600
_SALT = "cp-web-session"


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("PIPELINE_WEB_SECRET")
    if not secret:
        raise RuntimeError("PIPELINE_WEB_SECRET is not set")
    return URLSafeTimedSerializer(secret, salt=_SALT)


def issue_session(user_id: str) -> str:
    return _serializer().dumps(user_id)


def verify_session(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=MAX_AGE_SEC)
    except (BadSignature, SignatureExpired, RuntimeError):
        return None
