"""Поллер: новые звонки → черновик в веб-очередь приёмки. В граф ничего не пишет —
запись делает только review_actions.accept, после того как владелец примет звонок в дашборде."""
import asyncio
import logging
import os
from dataclasses import dataclass

from datetime import date

from . import pipeline_client as cp
from . import mionika_auth
from . import alerts
from . import gate
from . import context_update as cu
from .classifier import classify
from .draft_schema import alternatives_of, uncertainty_text
from .dedup import recording_key
from .digest import commitments_to_digest_tasks
from .store import Store
from .telegram_bot import NullBot, TelegramBot
from . import file_backend
from . import notifier

_log = logging.getLogger(__name__)
POLL_INTERVAL_SEC = 60


@dataclass
class Config:
    cp_base: str
    cp_token: str
    mionika_token: str
    anthropic_key: str
    db_path: str
    bot_token: str
    owner_chat_id: str
    # По умолчанию НЕ трогаем job в call-pipeline: спека требует, чтобы расшифровка
    # оставалась там. Страница «Разбор» качает её именно оттуда (GET /jobs/{id}/transcript),
    # и удаление job'а сразу после укладки в очередь делало кнопки скачивания мёртвыми.
    skip_after_process: bool = False  # True — после своей копии skip(delete) job в call-pipeline
    mionika_creds_path: str = ""      # если задан — OAuth-refresh токена из этого файла
    producer: str = "ops-ingest@dev"   # версия-производитель для атрибуции proposed-задач
    dashboard_url: str = ""           # ссылка на страницу «Разбор» в пинге
    owner_user_id: str = ""            # id владельца в call-pipeline — сверяется веб-API дашборда
    # Режим без Мионики: проекты из файла, принятое — в папку (см. file_backend)
    projects_file: str = ""
    accepted_dir: str = ""


def refresh_mionika(cfg) -> None:
    """Обновить cfg.mionika_token из creds-файла (refresh-если-близко-к-истечению).

    getattr, а не прямое обращение: функцию зовёт и поллер, и веб-API, и в
    тестах конфиг бывает урезанным. Отсутствие пути к кредам — не повод ронять
    запрос: без обновления токен просто останется прежним.
    """
    if getattr(cfg, "mionika_creds_path", None):
        try:
            cfg.mionika_token = mionika_auth.token(cfg.mionika_creds_path)
        except Exception as e:
            _log.exception("mionika token refresh failed: %s", e)


def select_unprocessed(jobs: list[dict], store: Store) -> list[dict]:
    # все режимы (short и call) — один звонок = один job; повтор отсекаем по курсору
    return [j for j in jobs if not store.is_processed(j["job_id"])]


async def process_job(cfg: Config, bot, store: Store, job_id: str) -> int:
    detail: dict = {}
    try:
        detail = cp.get_job(cfg.cp_base, cfg.cp_token, job_id)
    except Exception:
        pass

    rkey = recording_key(detail.get("dedupe_key"))
    if rkey is not None and store.is_key_processed(rkey):
        _log.info("job %s skipped (recording key %r already processed)", job_id, rkey)
        store.mark_processed(job_id, detail.get("transcript_text", ""))
        if cfg.skip_after_process:
            try:
                cp.delete_job(cfg.cp_base, cfg.cp_token, job_id)
            except Exception as e:
                _log.exception("skip(delete) failed for %s: %s", job_id, e)
        return 0

    payload = cp.get_transcript_payload(cfg.cp_base, cfg.cp_token, job_id)
    text = cp.render_transcript(payload)
    facts = cp.transcript_facts(payload)

    projects = await file_backend.fetch_projects(cfg)
    draft = await classify(text, projects, cfg.anthropic_key)
    tasks = commitments_to_digest_tasks(draft, job_id=job_id)
    extracted = await cu.extract_atoms(text, cfg.anthropic_key,
                                       today=date.today().isoformat(), tasks=tasks)

    origin = detail.get("origin") or "unknown"
    flags = gate.evaluate(draft, facts, origin)

    store.mark_processed(job_id, text)   # своя копия транскрипта — до всего остального
    store.upsert_queue_row(
        job_id,
        source=f"call:{job_id}",
        summary=draft.summary,
        project=(draft.primary_project.key if draft.primary_project else ""),
        confidence=(draft.primary_project.confidence if draft.primary_project else None),
        alternatives=alternatives_of(draft),
        uncertainty=uncertainty_text(draft),
        tasks=tasks,
        atoms=extracted["atoms"],
        count=len(extracted["atoms"]) + len(tasks),
        recorded_at=detail.get("recorded_at"),
        origin=origin,
        gate_flags=flags,
        diarized=facts["diarized"],
        speaker_count=facts["speaker_count"],
        unknown_share=facts["unknown_share"],
        owner_identified=draft.owner_identified,
        status="awaiting",
    )

    # Дедуп и очистка call-pipeline — ТОЛЬКО после того, как своя копия транскрипта
    # и строка очереди уже на месте (mark_processed/upsert_queue_row выше): если
    # процесс упадёт между ними и этим блоком, тот же звонок придёт снова и будет
    # разобран ещё раз — это безопаснее, чем пометить recording-key обработанным
    # и потерять разбор молча.
    if rkey is not None:
        store.mark_key_processed(rkey)
    if cfg.skip_after_process:
        try:
            cp.delete_job(cfg.cp_base, cfg.cp_token, job_id)
        except Exception as e:
            _log.exception("skip(delete) failed for %s: %s", job_id, e)

    _log.info("job %s -> в очередь: %d атомов, %d задач (флаги: %s)",
              job_id, len(extracted["atoms"]), len(tasks), ",".join(flags) or "нет")
    return len(extracted["atoms"])


async def tick(cfg: Config, bot, store: Store) -> int:
    """Один проход по новым job'ам. Возврат — сумма атомов, извлечённых process_job
    по каждому (до задачи 13 здесь считались задачи дайджеста; семантика возврата
    process_job поменялась вместе с переходом на веб-очередь, суммирование — нет)."""
    jobs = cp.list_processable(cfg.cp_base, cfg.cp_token)
    total = 0
    for j in select_unprocessed(jobs, store):
        try:
            total += await process_job(cfg, bot, store, j["job_id"])
            alerts.job_recovered(store, j["job_id"])
        except Exception as e:  # один битый звонок не валит весь тик
            _log.exception("job %s failed: %s", j["job_id"], e)
            # Журнала мало: 07.09 поток вставал трижды за день, и каждый раз это
            # замечалось случайно при чтении логов. Сигнал уходит в бот.
            alerts.job_failed(cfg, bot, store, j["job_id"], e)
    return total


async def run_cycle(cfg: Config, bot, store: Store) -> int:
    """Один цикл поллера: обновить токен, разобрать новые job'ы, напомнить про очередь.

    Пинг — здесь, а не в process_job. ping_pending — напоминалка про ВСЮ очередь
    ожидающих (с часовым кулдауном), а не уведомление о конкретном новом звонке:
    пинг из process_job молчал бы вечером и в выходные, когда новых звонков нет,
    а очередь стоит непустая, и на пачке из нескольких звонков за тик считал бы
    занижено — первый job пингует, остальные съедает кулдаун. Здесь пинг видит
    честное состояние очереди после всего тика.

    Вынесено из run_loop отдельной функцией — тестируемый шов без бесконечного цикла."""
    refresh_mionika(cfg)
    total = await tick(cfg, bot, store)
    try:
        notifier.ping_pending(cfg, bot, store)
    except Exception as e:
        _log.exception("ping_pending failed: %s", e)
    return total


async def run_loop(cfg: Config, bot, store: Store):
    logging.basicConfig(level=logging.INFO)
    while True:
        await run_cycle(cfg, bot, store)
        await asyncio.sleep(POLL_INTERVAL_SEC)


def config_from_env() -> Config:
    creds_path = os.environ.get("OPS_INGEST_MIONIKA_CREDS", "")
    mionika_token = (mionika_auth.token(creds_path) if creds_path
                     else os.environ.get("OPS_INGEST_MIONIKA_TOKEN", ""))
    return Config(
        cp_base=os.environ["OPS_INGEST_CP_BASE"],
        cp_token=os.environ["OPS_INGEST_CP_TOKEN"],
        mionika_token=mionika_token,
        mionika_creds_path=creds_path,
        anthropic_key=os.environ["ANTHROPIC_API_KEY"],
        db_path=os.environ.get("OPS_INGEST_DB", "state.db"),
        bot_token=os.environ.get("OPS_INGEST_BOT_TOKEN", ""),
        owner_chat_id=os.environ.get("OPS_INGEST_OWNER_CHAT_ID", ""),
        skip_after_process=os.environ.get("OPS_INGEST_SKIP_AFTER_PROCESS", "0")
        not in ("0", "false", "False", ""),
        producer=os.environ.get("OPS_INGEST_PRODUCER", "ops-ingest@dev"),
        dashboard_url=os.environ.get("OPS_INGEST_DASHBOARD_URL", ""),
        owner_user_id=os.environ.get("OPS_INGEST_OWNER_USER_ID", ""),
        projects_file=os.environ.get("OPS_INGEST_PROJECTS_FILE", ""),
        accepted_dir=os.environ.get("OPS_INGEST_ACCEPTED_DIR", ""),
    )


async def _main():
    cfg = config_from_env()
    bot = TelegramBot(cfg.bot_token) if cfg.bot_token else NullBot()
    store = Store(cfg.db_path)
    await run_loop(cfg, bot, store)


if __name__ == "__main__":
    asyncio.run(_main())
