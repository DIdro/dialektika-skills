"""Промпты для проходов над звонком (Task 13: путь через structure-discovering
submit_context/propose_context снят вместе с backfill.py — единственным держателем)."""

# --- Проход A: извлечение атомов без проекта ---------------------------------
# Проект и скелет узлов сюда не попадают: раскладкой по узлам занимается отдельный
# шаг (проход B), а черновик из этого прохода должен переживать смену проекта.

EXTRACT_TOOL_NAME = "submit_atoms"

_EXTRACT_SYS = """\
Ты выделяешь из транскрипта звонка то, что должно попасть в базу знаний как контекст.
1. Выдели НОВОЕ: решения, риски, сдвиги roadmap, важную ценность и факты.
2. Для каждого пункта укажи тип (decision/risk/value/roadmap/note) и краткий текст (1–2 предложения).
3. Проект и его структура тебе НЕ даны и не нужны — раскладкой по узлам занимается отдельный шаг.
   Не выдумывай, куда это положить.
4. Свежесть: latest_date — самая поздняя дата, обсуждавшаяся в звонке как актуальная
   (ISO YYYY-MM-DD, лучшая оценка года по контексту; null если дат нет). is_stale=true,
   если звонок явно старый ОТНОСИТЕЛЬНО даты «Сегодня» из контекста.
5. Если дан список ЗАДАЧ из звонка — НЕ переписывай и НЕ дублируй их в atoms.
Верни всё через инструмент submit_atoms ровно один раз.
"""


def _extract_tool_schema() -> dict:
    return {"name": EXTRACT_TOOL_NAME,
            "description": "Submit extracted atoms + recency. Call once.",
            "input_schema": {"type": "object", "properties": {
                "atoms": {"type": "array", "items": {"type": "object", "properties": {
                    "kind": {"type": "string",
                             "enum": ["decision", "risk", "value", "roadmap", "note"]},
                    "text": {"type": "string"}}, "required": ["kind", "text"]}},
                "recency": {"type": "object", "properties": {
                    "is_stale": {"type": "boolean"},
                    "date_hint": {"type": ["string", "null"]},
                    "latest_date": {"type": ["string", "null"]}},
                    "required": ["is_stale"]}},
                "required": ["atoms", "recency"]}}


def build_extract_messages(*, transcript, today=None, tasks=None) -> dict:
    head = f"Сегодня: {today}\n\n" if today else ""
    user = f"{head}Транскрипт:\n\n{transcript}\n\nВызови {EXTRACT_TOOL_NAME}."
    if tasks:
        tl = "\n".join(f"{i}. {t.get('title', '')}" for i, t in enumerate(tasks))
        user = (f"{head}Транскрипт:\n\n{transcript}\n\n"
                f"Задачи из звонка (их дублировать в atoms НЕ надо):\n{tl}\n\n"
                f"Вызови {EXTRACT_TOOL_NAME}.")
    return {
        "system": [{"type": "text", "text": _EXTRACT_SYS,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "tools": [_extract_tool_schema()],
        "tool_choice": {"type": "tool", "name": EXTRACT_TOOL_NAME},
    }


# --- Проход B: раскладка готовых атомов по узлам проекта ---------------------
# На вход — короткие строки атомов и скелет проекта, а НЕ транскрипт: раскладка
# запускается в момент приёмки звонка в дашборде, и это на порядки дешевле.

ROUTE_TOOL_NAME = "submit_routes"

_ROUTE_SYS = """\
Тебе даны СКЕЛЕТ проекта (узлы-контейнеры: shortId, назначение, примеры уже лежащего в них)
и список готовых атомов, извлечённых из звонка. Разложить их по узлам:
1. Для каждого атома укажи СУЩЕСТВУЮЩИЙ узел (shortId ИЗ СКЕЛЕТА). Не выдумывай узлы;
   нет подходящего — node="NONE".
2. ДЕДУП: если в children узла уже есть это по смыслу — положи индекс атома в duplicates
   и узел ему всё равно назначь.
3. ПРОТИВОРЕЧИЯ: если атом не дополняет, а СПОРИТ с тем, что уже лежит в узле — добавь запись
   в contradictions с индексом атома, цитатой существующего и одной строкой, в чём конфликт.
   Не путай противоречие с уточнением или развитием.
4. Для каждой ЗАДАЧИ укажи узел в task_routes; нет подходящего — node="NONE".
Верни всё через инструмент submit_routes ровно один раз.
"""


def _route_tool_schema() -> dict:
    _node = {"type": "string", "description": "shortId узла из скелета или 'NONE'"}
    return {"name": ROUTE_TOOL_NAME, "description": "Submit node routing. Call once.",
            "input_schema": {"type": "object", "properties": {
                "atom_nodes": {"type": "array", "items": {"type": "object", "properties": {
                    "index": {"type": "integer"}, "node": _node},
                    "required": ["index", "node"]}},
                "task_routes": {"type": "array", "items": {"type": "object", "properties": {
                    "index": {"type": "integer"}, "node": _node},
                    "required": ["index", "node"]}},
                "contradictions": {"type": "array", "items": {"type": "object", "properties": {
                    "index": {"type": "integer"}, "existing": {"type": "string"},
                    "note": {"type": "string"}}, "required": ["index", "note"]}},
                "duplicates": {"type": "array", "items": {"type": "integer"}}},
                "required": ["atom_nodes"]}}


def build_route_messages(*, project_title, skeleton, atoms, tasks) -> dict:
    skel_lines = []
    for n in skeleton:
        ch = "; ".join(n.get("children") or [])
        skel_lines.append(f"{n['shortId']}: {n['title']}" + (f"  [уже: {ch}]" if ch else ""))
    skel_block = f"Проект: {project_title}\nСкелет (узлы-контейнеры):\n" + "\n".join(skel_lines)

    al = "\n".join(f"{i}. [{a.get('kind', 'note')}] {a.get('text', '')}"
                   for i, a in enumerate(atoms or []))
    user = f"Атомы (нумерация с 0):\n{al}\n"
    if tasks:
        tl = "\n".join(f"{i}. {t.get('title', '')}" for i, t in enumerate(tasks))
        user += f"\nЗадачи (нумерация с 0):\n{tl}\n"
    user += f"\nВызови {ROUTE_TOOL_NAME}."

    return {
        "system": [{"type": "text", "text": _ROUTE_SYS, "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": skel_block, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "tools": [_route_tool_schema()],
        "tool_choice": {"type": "tool", "name": ROUTE_TOOL_NAME},
    }


# --- Разнесение готовых атомов по нескольким проектам по инструкции владельца ---
# На вход — атомы звонка (не транскрипт) и список проектов: разнесение запускается
# из дашборда, когда владелец решил, что звонок затрагивает не один проект.

SPLIT_TOOL_NAME = "submit_split"

_SPLIT_SYS = """\
Тебе даны атомы, извлечённые из одного звонка, список проектов и инструкция владельца
записи, как их разнести. Задача:
1. Для КАЖДОГО атома укажи project_key — строго из данного списка проектов.
   Не выдумывай ключи, не оставляй атом без проекта.
2. Так же разнеси задачи, если они даны.
3. Инструкция владельца — главнее твоей догадки. Если она называет проекты явно,
   распределяй только между ними.
Верни всё через инструмент submit_split ровно один раз.
"""


def _split_tool_schema() -> dict:
    _item = {"type": "object", "properties": {
        "index": {"type": "integer"}, "project_key": {"type": "string"}},
        "required": ["index", "project_key"]}
    return {"name": SPLIT_TOOL_NAME, "description": "Submit per-atom project split. Call once.",
            "input_schema": {"type": "object", "properties": {
                "atom_projects": {"type": "array", "items": _item},
                "task_projects": {"type": "array", "items": _item}},
                "required": ["atom_projects"]}}


def build_split_messages(*, atoms, tasks, projects, instruction) -> dict:
    pl = "\n".join(f"{p.get('key')} — {p.get('name', '')}" for p in projects)
    al = "\n".join(f"{i}. [{a.get('kind', 'note')}] {a.get('text', '')}"
                   for i, a in enumerate(atoms or []))
    user = f"Инструкция владельца записи:\n{instruction}\n\nАтомы (нумерация с 0):\n{al}\n"
    if tasks:
        tl = "\n".join(f"{i}. {t.get('title', '')}" for i, t in enumerate(tasks))
        user += f"\nЗадачи (нумерация с 0):\n{tl}\n"
    user += f"\nВызови {SPLIT_TOOL_NAME}."
    return {
        "system": [{"type": "text", "text": _SPLIT_SYS, "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": f"Проекты:\n{pl}",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "tools": [_split_tool_schema()],
        "tool_choice": {"type": "tool", "name": SPLIT_TOOL_NAME},
    }
