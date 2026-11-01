"""Решения владельца по строке очереди. ЕДИНСТВЕННОЕ место, пишущее в Мионику
в рабочем потоке — всё остальное живёт в SQLite до подтверждения."""
import asyncio
import logging
from datetime import datetime, timezone

from . import context_update as cu
from . import file_backend
from . import mionika_client as mio
from . import pipeline_client as cp
from .classifier import classify
from .draft_schema import alternatives_of, uncertainty_text
from .digest import commitments_to_digest_tasks

_log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _base_job_id(job_id: str) -> str:
    """Строка-потомок после split имеет id вида '<job_id>#<project_key>'. Своя копия
    транскрипта в call-pipeline и в processed лежит под БАЗОВЫМ id — решётку нужно
    срезать до любого обращения к ним, иначе httpx считает её началом URL-фрагмента
    и запрос молча уезжает по чужому пути."""
    return job_id.split("#", 1)[0]


def _is_child(job_id: str) -> bool:
    return "#" in job_id


def _children(store, base_id: str) -> list[dict]:
    """Все строки очереди, порождённые split() от base_id (id вида 'base_id#project')."""
    prefix = f"{base_id}#"
    return [r for r in store.list_queue() if r["job_id"].startswith(prefix)]


async def accept(cfg, store, job_id, *, force=False) -> dict:
    """Разложить атомы по узлам выбранного проекта и записать их в граф.

    Противоречие с уже лежащим в узле — единственное, что останавливает запись:
    его видно только здесь, потому что для него нужно содержимое проекта.
    """
    row = store.get_queue_row(job_id)
    if row is None:
        raise LookupError(job_id)
    # Единственный гейт для всех пяти действий: работаем только со строкой в
    # awaiting. Это закрывает и повторную запись после split (статус split уже
    # не awaiting), и гонку с reparse (тот выставляет reparsing синхронно перед
    # сетевыми вызовами) — без отдельного списка «терминальных» статусов.
    if row["status"] != "awaiting":
        return {"already": row["status"]}
    project = (row.get("project") or "").strip()
    if not project:
        return {"error": "project_not_set"}

    if getattr(cfg, "accepted_dir", ""):
        written = file_backend.write_accepted(cfg.accepted_dir, _base_job_id(job_id), project, row)
        store.upsert_queue_row(job_id, status="accepted", decided_at=_now(), count=written)
        _log.info("accepted %s -> %s (%d пунктов в файлы)", job_id, project, written)
        return {"written": written, "contradictions": []}

    project_id = await mio.resolve_project_id(cfg.mionika_token, project)
    routes = await cu.route_atoms(cfg.mionika_token, project_id, project,
                                  row["atoms"], row["tasks"], cfg.anthropic_key)

    contradictions = routes.get("contradictions") or []
    if contradictions and not force:
        return {"needs_confirm": True, "contradictions": contradictions}

    # ref строим от БАЗОВОГО id (без "#потомок"), а source — от исходного значения
    # строки: у потомков после split row["source"] унаследован от родителя
    # ("call:j1"), и именно он должен уехать в граф — иначе группировка «все атомы
    # этого звонка» ломается, а решётка в ref — риск на стороне валидации Мионики.
    base_id = _base_job_id(job_id)
    source = row.get("source") or f"call:{base_id}"
    ops = cu.atoms_to_ops(row["atoms"], routes["atom_nodes"],
                          routes.get("duplicates") or [], base_id, source=source)
    ops += cu.tasks_to_ops(row["tasks"], routes.get("task_routes") or [], base_id,
                           source=source)
    if ops:
        await mio.write_proposed(cfg.mionika_token, project_id, ops)
    store.upsert_queue_row(job_id, status="accepted", decided_at=_now(), count=len(ops))
    _log.info("accepted %s -> %s (%d ops)", job_id, project, len(ops))
    return {"written": len(ops), "contradictions": contradictions}


async def reject(cfg, store, job_id) -> dict:
    """Черновик выбрасывается. В граф не уезжает ничего; транскрипт в
    call-pipeline не трогаем."""
    row = store.get_queue_row(job_id)
    if row is None:
        raise LookupError(job_id)
    if row["status"] != "awaiting":
        return {"already": row["status"]}
    store.upsert_queue_row(job_id, status="rejected", decided_at=_now(),
                           atoms=[], tasks=[])
    return {"rejected": True}


def retarget(store, job_id, project_key: str) -> dict:
    """Смена проекта. LLM не вызывается: раскладка по узлам живёт в accept,
    поэтому до нажатия менять нечего — правится один ярлык."""
    row = store.get_queue_row(job_id)
    if row is None:
        raise LookupError(job_id)
    if row["status"] != "awaiting":
        return {"already": row["status"]}
    store.upsert_queue_row(job_id, project=project_key, confidence=None)
    return {"project": project_key}


def _distribute(by_project, items, recs, valid, bucket, label, job_id):
    """Раскладывает items (атомы или задачи) по by_project[project_key][bucket]
    согласно recs модели. Ничего не роняет молча: каждый отброшенный элемент —
    неизвестный проект, индекс вне диапазона или индекс, который модель вообще
    не упомянула, — идёт в лог и в счётчик. Тихая потеря извлечённого материала —
    ровно то, ради устранения чего вся эта перестройка и делается."""
    dropped = 0
    seen = set()
    for rec in recs or []:
        idx = rec.get("index")
        key = rec.get("project_key")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            _log.warning("split %s: %s с индексом %r отброшен — индекс вне диапазона",
                         job_id, label, idx)
            dropped += 1
            continue
        seen.add(idx)
        if key not in valid:
            _log.warning("split %s: %s %d отброшен — неизвестный проект %r",
                         job_id, label, idx, key)
            dropped += 1
            continue
        by_project.setdefault(key, {"atoms": [], "tasks": []})
        by_project[key][bucket].append(items[idx])
    for i in range(len(items)):
        if i not in seen:
            _log.warning("split %s: %s %d отброшен — модель не упомянула его",
                         job_id, label, i)
            dropped += 1
    return dropped


async def split(cfg, store, job_id, prompt: str) -> dict:
    """Разнести атомы звонка по нескольким проектам по инструкции владельца.

    Работает с ГОТОВЫМИ атомами, а не с транскриптом: на вход модели идут
    ~18 коротких строк вместо 9 200 токенов расшифровки.

    Гейт мягче, чем у остальных четырёх действий: помимо awaiting разрешён
    и повторный вызов на строке в статусе split — разнесение никогда не пишет
    в граф, а атомы родителя split не чистит, поэтому переиграть раскладку
    другой инструкцией безопасно. Единственное условие — среди прежних
    потомков не должно быть уже принятых (см. ниже), иначе их атомы уедут
    в граф второй раз при следующем accept.

    Только для строки первого уровня: разнесение ПОТОМКА обходит эту же защиту.
    Проверка принятых потомков смотрит на прямые потомки ("base#key"), а
    осиротевшими помечаются только ждущие — значит уже разнесённый потомок
    ("base#key", статус split) вместе со своим поддеревом ("base#key#key2")
    переживает переразнесение родителя, и его собственных потомков потом можно
    принять рядом с новой раскладкой: тот же материал уедет в граф дважды.
    Двухуровневое разнесение никому не нужно, поэтому закрываем его целиком,
    а не чиним обход.
    """
    if _is_child(job_id):
        return {"error": "split_on_child"}
    row = store.get_queue_row(job_id)
    if row is None:
        raise LookupError(job_id)
    if row["status"] not in ("awaiting", "split"):
        return {"already": row["status"]}

    resplitting = row["status"] == "split"
    children = _children(store, job_id) if resplitting else []
    if resplitting and any(c["status"] == "accepted" for c in children):
        return {"error": "split_has_accepted_children"}

    # Полный список проектов (key+name), а не голые ключи: промпт рендерит
    # "key — name", и без name модель видит слаги с висящим тире — точность
    # разнесения (единственный смысл этого вызова) от этого страдает.
    projects = await file_backend.fetch_projects(cfg)
    valid = {p["key"] for p in projects}
    plan = await cu.split_atoms(row["atoms"], row["tasks"], projects, prompt,
                                cfg.anthropic_key)

    by_project: dict[str, dict] = {}
    dropped = _distribute(by_project, row["atoms"], plan["atom_projects"], valid,
                          "atoms", "атом", job_id)
    dropped += _distribute(by_project, row["tasks"], plan.get("task_projects") or [],
                           valid, "tasks", "задача", job_id)
    if not by_project:
        return {"error": "split_produced_nothing", "dropped": dropped}

    if len(by_project) == 1:
        # Всё уцелевшее ушло в один проект — это смена проекта, а не разнесение.
        # Ветку не порождаем: Так уже было: модель унесла все 31 атом звонка
        # в один проект, и в очереди встали родитель «разнесено» и ветка с тем же
        # саммари и теми же атомами, что читалось как дубль. Строка возвращается
        # в awaiting с новым проектом и только тем материалом, что модель отнесла
        # (отброшенное — как и у веток — не остаётся). Ждущие ветки прежней
        # раскладки закрываются: принятых среди них нет, проверено выше.
        (key, part), = by_project.items()
        for c in children:
            if c["status"] == "awaiting":
                store.upsert_queue_row(c["job_id"], status="rejected", decided_at=_now())
        store.upsert_queue_row(
            job_id, status="awaiting", decided_at=None, project=key, confidence=None,
            atoms=part["atoms"], tasks=part["tasks"],
            count=len(part["atoms"]) + len(part["tasks"]), reparse_prompt=prompt)
        return {"projects": [key], "dropped": dropped, "retargeted": True}

    if resplitting:
        # Прежние потомки, которых новая раскладка больше не породит, — осиротевшие
        # строки от старого разнесения. Принятых среди них нет (проверили выше),
        # поэтому переводим их в rejected: очередь не должна зарастать строками,
        # которые уже никуда не ведут. Потомков с совпавшим ключом просто
        # перезапишет цикл ниже — тот же job_id, свежие atoms/tasks.
        new_child_ids = {f"{job_id}#{key}" for key in by_project}
        for c in children:
            if c["status"] == "awaiting" and c["job_id"] not in new_child_ids:
                store.upsert_queue_row(c["job_id"], status="rejected", decided_at=_now())

    for key, part in by_project.items():
        store.upsert_queue_row(
            f"{job_id}#{key}", source=row["source"], summary=row["summary"],
            project=key, confidence=None, atoms=part["atoms"], tasks=part["tasks"],
            count=len(part["atoms"]) + len(part["tasks"]),
            recorded_at=row["recorded_at"], origin=row["origin"],
            gate_flags=row["gate_flags"], diarized=row["diarized"],
            speaker_count=row["speaker_count"], unknown_share=row["unknown_share"],
            owner_identified=row["owner_identified"], status="awaiting",
            reparse_prompt=prompt)
    # Статус исходной строки — "split", а НЕ "rejected": метрики (доля переигранного)
    # считают его относительно accepted/rejected, и разнесение звонка на проекты —
    # не решение о качестве разбора, а расщепление строки на потомков. "rejected"
    # здесь врал бы метрике вверх (как будто владелец забраковал разбор).
    store.upsert_queue_row(job_id, status="split", decided_at=_now(),
                           reparse_prompt=prompt)
    return {"projects": sorted(by_project), "dropped": dropped}


async def reparse(cfg, store, job_id, prompt: str) -> dict:
    """Полный переразбор транскрипта с инструкцией. Дороже всего — только когда
    кривы сами атомы.

    Только для целого звонка: у потомка после split ("j1#demo-bot") нет своей
    транскрипт-истории — есть только транскрипт ВСЕГО звонка (он лежит под
    базовым id). Переразбор потомка тихо перезаписал бы его atoms и project
    материалом всего звонка, а не его долей: сосед-потомок хранит свою часть,
    и после приёмки обоих один и тот же материал уехал бы в граф дважды, в два
    проекта, под одним источником — тот самый класс дефекта, ради которого
    введён гейт статуса. Правильный путь исправить неудачное разнесение —
    повторный split на родителе (см. split()), не reparse на потомке.
    """
    if _is_child(job_id):
        return {"error": "reparse_on_child"}
    row = store.get_queue_row(job_id)
    if row is None:
        raise LookupError(job_id)
    if row["status"] != "awaiting":
        return {"already": row["status"]}
    store.upsert_queue_row(job_id, status="reparsing", reparse_prompt=prompt)
    try:
        # job_id тут гарантированно без "#" (проверили выше) — это id ВСЕГО звонка,
        # под ним же лежит и своя копия транскрипта, и job call-pipeline.
        text = store.get_transcript(job_id) or await asyncio.to_thread(
            cp.get_transcript_text, cfg.cp_base, cfg.cp_token, job_id)
        projects = await file_backend.fetch_projects(cfg)
        draft = await classify(text, projects, cfg.anthropic_key, extra_instruction=prompt)
        tasks = commitments_to_digest_tasks(draft, job_id=job_id)
        extracted = await cu.extract_atoms(text, cfg.anthropic_key, tasks=tasks)
    except Exception:
        # Статус ставился ДО сетевых вызовов и не откатывался бы сам: таймаут модели,
        # 404 транскрипта, отвал MCP — и звонок молча выпадал бы из очереди (веб тянет
        # только awaiting, кнопок на "reparsing" нет). Возвращаем в awaiting и
        # пробрасываем ошибку — вызывающий код и так должен её увидеть.
        store.upsert_queue_row(job_id, status="awaiting")
        raise
    store.upsert_queue_row(
        job_id, status="awaiting", summary=draft.summary,
        project=(draft.primary_project.key if draft.primary_project else ""),
        confidence=(draft.primary_project.confidence if draft.primary_project else None),
        alternatives=alternatives_of(draft), uncertainty=uncertainty_text(draft),
        atoms=extracted["atoms"], tasks=tasks,
        count=len(extracted["atoms"]) + len(tasks))
    return {"atoms": len(extracted["atoms"])}
