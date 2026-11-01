"""LLM-проходы над звонком: извлечение атомов (A), раскладка по узлам проекта (B),
разнесение по нескольким проектам (split). Запись в граф сюда не входит — она
единственно живёт в review_actions.accept."""
from datetime import date

from anthropic import AsyncAnthropic

from .context_prompt import (build_extract_messages, build_route_messages,
                             build_split_messages,
                             EXTRACT_TOOL_NAME, ROUTE_TOOL_NAME, SPLIT_TOOL_NAME)
from .project_skeleton import fetch_skeleton

MODEL = "claude-sonnet-4-6"
STALE_MAX_AGE_DAYS = 21


def is_stale_by_date(latest_date, today, max_age_days=STALE_MAX_AGE_DAYS) -> bool:
    """Детерминированно: звонок старый, если самая поздняя обсуждавшаяся дата старше today
    более чем на max_age_days. Не распарсилось/нет даты → False (не скипаем вслепую)."""
    try:
        ld = date.fromisoformat(str(latest_date)[:10])
        td = date.fromisoformat(str(today)[:10])
    except (ValueError, TypeError):
        return False
    return (td - ld).days > max_age_days


def decide_stale(recorded_at, latest_date, llm_is_stale, today) -> bool:
    """recorded_at (реальная дата записи) — авторитетна, если есть. Иначе — оценка по тексту
    (LLM is_stale ИЛИ дата из транскрипта)."""
    if recorded_at:
        return is_stale_by_date(recorded_at, today)
    return bool(llm_is_stale) or (today is not None and is_stale_by_date(latest_date, today))


def extract_updates(tool_input: dict) -> dict:
    return {"updates": tool_input.get("updates", []), "recency": tool_input.get("recency", {}),
            "task_routes": tool_input.get("task_routes", [])}


def tasks_to_ops(tasks, task_routes, job_id, *, source=None):
    """Задачи звонка → create-ops type=task. Маршрут NONE/нет маршрута → атом без links
    (сирота, но видна в графе по disposition+source — задачу нельзя молча потерять).

    source: явное значение metadata.source — например, унаследованное от родителя после
    review_actions.split ("call:j1" у всех потомков "j1#<project>"). По умолчанию строится
    как f"call:{job_id}", как раньше."""
    routes = {r.get("index"): (r.get("node") or "").strip() for r in (task_routes or [])}
    src = source or f"call:{job_id}"
    ops = []
    for i, t in enumerate(tasks or []):
        title = ("[мягкое] " if t.get("soft") else "") + (t.get("title") or "").strip()
        if not title.strip():
            continue
        md = {"taskStatus": "backlog", "disposition": "accepted",
              "source": src, "owner": t.get("owner") or "owner"}
        if t.get("activity_type"):
            md["activityType"] = t["activity_type"]
        if t.get("deadline"):
            md["deadline"] = t["deadline"]
        if t.get("commitment_to"):
            md["commitmentTo"] = t["commitment_to"]
        op = {"ref": f"task_{job_id}_{i}", "action": "create_atom", "type": "task",
              "title": title[:120], "content": title, "metadata": md}
        node = routes.get(i, "NONE")
        if node and node.upper() != "NONE":
            op["links"] = [{"type": "contains", "from": node}]
        ops.append(op)
    return ops


def _tool_input(content_blocks, tool_name):
    for b in content_blocks:
        if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == tool_name:
            return b.input
    raise RuntimeError(f"нет tool_use {tool_name} в ответе модели")


async def extract_atoms(transcript, api_key, *, model=MODEL, today=None, tasks=None) -> dict:
    """Проход A: транскрипт → атомы. Проект и его скелет не участвуют — черновик
    должен переживать смену проекта в дашборде."""
    payload = build_extract_messages(transcript=transcript, today=today, tasks=tasks)
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(model=model, max_tokens=3000, system=payload["system"],
                                        messages=payload["messages"], tools=payload["tools"],
                                        tool_choice=payload["tool_choice"])
    data = _tool_input(resp.content, EXTRACT_TOOL_NAME)
    return {"atoms": data.get("atoms", []), "recency": data.get("recency", {})}


async def route_atoms(access_token, project_id, project_title, atoms, tasks, api_key,
                      *, model=MODEL) -> dict:
    """Проход B: атомы + скелет проекта → узлы, дубли и противоречия.
    Транскрипт не читается — на вход идут короткие строки атомов."""
    skeleton = await fetch_skeleton(access_token, project_id)
    payload = build_route_messages(project_title=project_title, skeleton=skeleton,
                                   atoms=atoms, tasks=tasks)
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(model=model, max_tokens=2000, system=payload["system"],
                                        messages=payload["messages"], tools=payload["tools"],
                                        tool_choice=payload["tool_choice"])
    data = _tool_input(resp.content, ROUTE_TOOL_NAME)
    return {"atom_nodes": data.get("atom_nodes", []),
            "task_routes": data.get("task_routes", []),
            "contradictions": data.get("contradictions", []),
            "duplicates": data.get("duplicates", [])}


def atoms_to_ops(atoms, atom_nodes, duplicates, job_id, *, source=None) -> list[dict]:
    """Атомы черновика → create-ops. disposition=accepted: в граф попадает
    только то, что владелец подтвердил в дашборде.

    source: явное значение metadata.source — например, унаследованное от родителя после
    review_actions.split ("call:j1" у всех потомков "j1#<project>"). По умолчанию строится
    как f"call:{job_id}", как раньше."""
    nodes = {r.get("index"): (r.get("node") or "").strip() for r in (atom_nodes or [])}
    dup = set(duplicates or [])
    src = source or f"call:{job_id}"
    ops = []
    for i, a in enumerate(atoms or []):
        if i in dup:
            continue
        text = (a.get("text") or "").strip()
        if not text:
            continue
        kind = a.get("kind") or "note"
        op = {
            # явный уникальный ref: с inline-links Mionika иначе генерит авто-ref по
            # таймстемпу и несколько атомов в одном батче коллизятся ("Duplicate batch ref").
            "ref": f"ctx_{job_id}_{i}",
            "action": "create_atom",
            "type": "decision" if kind == "decision" else "note",
            "title": text[:60], "content": text,
            # trustStatus обязателен для knowledge-типов в trust-workflow Mionika —
            # без валидного значения batch_crud падает 409 INVALID_METADATA.
            # Потолок машины — pending_review: verified ставит только владелец касанием.
            "metadata": {"disposition": "accepted", "trustStatus": "pending_review",
                         "source": src, "contextKind": kind},
        }
        node = nodes.get(i, "NONE")
        if node and node.upper() != "NONE":
            op["links"] = [{"type": "contains", "from": node}]
        ops.append(op)
    return ops


async def split_atoms(atoms, tasks, projects, instruction, api_key, *, model=MODEL) -> dict:
    """Разнести готовые атомы по нескольким проектам. Транскрипт не читается."""
    payload = build_split_messages(atoms=atoms, tasks=tasks, projects=projects,
                                   instruction=instruction)
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(model=model, max_tokens=2000, system=payload["system"],
                                        messages=payload["messages"], tools=payload["tools"],
                                        tool_choice=payload["tool_choice"])
    data = _tool_input(resp.content, SPLIT_TOOL_NAME)
    return {"atom_projects": data.get("atom_projects", []),
            "task_projects": data.get("task_projects", [])}
