"""HTTP-поверхность очереди приёмки. Читает ту же SQLite, что и демон разбора.

Своей авторизации нет: cookie-сессия call-pipeline прокидывается в его же /web/me,
и пользователь должен оказаться владельцем. Отдельного логина у дашборда не появляется.
"""
import logging

import anthropic
import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import file_backend
from . import review_actions as ra
from .run import refresh_mionika

COOKIE_NAME = "cp_session"

_log = logging.getLogger(__name__)


def model_error_text(exc: anthropic.APIError) -> str:
    """Причина отказа модели словами — для кнопки в дашборде.

    Так уже было: : кредиты кончились, accept/split падали голым 500 с телом-текстом,
    а в HTTP/2 у ответа нет statusText — дашборд показывал пустоту после двоеточия.
    """
    if "credit balance" in str(exc).lower():
        return ("Модель недоступна: кончились кредиты Anthropic. "
                "Пополните баланс (Plans & Billing) и повторите — звонок остался в очереди.")
    return (f"Модель недоступна ({type(exc).__name__}). "
            "Повторите позже — звонок остался в очереди.")


def fetch_me(cp_base: str, cookie: str | None) -> dict | None:
    """None — любой отказ: сети нет, статус не 200 или тело не разбирается как JSON.
    require_owner не различает эти случаи между собой (все они — «сессия не подтверждена»,
    401), поэтому разбор ответа не должен ронять процесс необработанным исключением —
    сервис проверки чужой, и его ответ доверия не заслуживает."""
    if not cookie:
        return None
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{cp_base}/web/me", cookies={COOKIE_NAME: cookie})
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


class RetargetBody(BaseModel):
    project_key: str


class PromptBody(BaseModel):
    prompt: str


class AcceptBody(BaseModel):
    force: bool = False


def create_app(cfg, store) -> FastAPI:
    def fresh_token():
        """Обновить токен Мионики перед обращением к ней.

        Access-токен живёт сутки. Поллер обновляет его каждым циклом
        (`run_cycle` → `refresh_mionika`), а этот процесс прочитал конфиг один
        раз при старте — и ровно через сутки все обращения к Мионике начинают
        отвечать 401. Так уже было: : выпадашка проектов опустела, причём
        молча, потому что фронт гасил ошибку и подставлял пустой список.
        """
        refresh_mionika(cfg)

    app = FastAPI(title="ops-ingest review queue")

    @app.exception_handler(anthropic.APIError)
    async def model_unavailable(_request, exc: anthropic.APIError):
        # Строка при этом остаётся в awaiting: accept и split зовут модель до записи
        # статуса, reparse откатывает свой reparsing сам.
        _log.error("модель недоступна: %s", exc)
        return JSONResponse(status_code=503, content={"detail": model_error_text(exc)})

    def require_owner(cp_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
        me = fetch_me(cfg.cp_base, cp_session)
        if not me:
            raise HTTPException(401, "нужна сессия call-pipeline")
        if me.get("id") != cfg.owner_user_id:
            raise HTTPException(403, "очередь разбора — личная")
        return me

    def _row_or_404(job_id: str) -> dict:
        row = store.get_queue_row(job_id)
        if row is None:
            raise HTTPException(404, "звонок не найден")
        return row

    @app.get("/reviews")
    def list_reviews(status: str | None = None, _=Depends(require_owner)):
        return {"reviews": store.list_queue(status=status)}

    @app.get("/reviews/{job_id}")
    def get_review(job_id: str, _=Depends(require_owner)):
        return _row_or_404(job_id)

    @app.post("/reviews/{job_id}/accept")
    async def accept(job_id: str, body: AcceptBody, _=Depends(require_owner)):
        _row_or_404(job_id)
        fresh_token()
        return await ra.accept(cfg, store, job_id, force=body.force)

    @app.post("/reviews/{job_id}/reject")
    async def reject(job_id: str, _=Depends(require_owner)):
        _row_or_404(job_id)
        return await ra.reject(cfg, store, job_id)

    @app.post("/reviews/{job_id}/retarget")
    def retarget(job_id: str, body: RetargetBody, _=Depends(require_owner)):
        _row_or_404(job_id)
        return ra.retarget(store, job_id, body.project_key)

    @app.post("/reviews/{job_id}/split")
    async def split(job_id: str, body: PromptBody, _=Depends(require_owner)):
        _row_or_404(job_id)
        fresh_token()
        return await ra.split(cfg, store, job_id, body.prompt)

    @app.post("/reviews/{job_id}/reparse")
    async def reparse(job_id: str, body: PromptBody, _=Depends(require_owner)):
        _row_or_404(job_id)
        fresh_token()
        return await ra.reparse(cfg, store, job_id, body.prompt)

    @app.get("/projects")
    async def projects(_=Depends(require_owner)):
        fresh_token()
        return {"projects": await file_backend.fetch_projects(cfg)}

    @app.get("/metrics")
    def metrics(_=Depends(require_owner)):
        return store.queue_metrics()

    return app
