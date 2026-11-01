"""Mionika. Write/projects через MCP streamable-http; чтение задач — REST /api/v1/atoms
(MCP list_atoms отдаёт лишь текстовую сводку без metadata, поэтому для фильтра по source
нужен REST, как в planday.fetch_board)."""
import asyncio
import logging

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_log = logging.getLogger(__name__)
MCP_URL = "https://mionika.ryefield.com/mcp"
REST_BASE = "https://mionika.ryefield.com"


def parse_projects_text(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        parts = [p.strip() for p in raw.strip().split("|")]
        if len(parts) >= 2 and parts[0]:
            out.append({"key": parts[0], "name": parts[1]})
    return out


def _projects_from_result(result) -> list[dict]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("projects"), list):
        return [{"key": p.get("key"), "name": p.get("name", p.get("key"))}
                for p in structured["projects"] if isinstance(p, dict) and p.get("key")]
    out = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "text":
            out.extend(parse_projects_text(getattr(block, "text", "") or ""))
    return out


class _McpSession:
    """Обёртка над MCP-сессией: каждый call_tool сам открывает соединение,
    делает один вызов и закрывает — ровно так, как раньше это делал целиком
    fetch_projects. Пагинация просто зовёт call_tool несколько раз подряд,
    жизненный цикл соединения (открытие/закрытие) не меняется."""

    def __init__(self, access_token: str):
        self._access_token = access_token

    async def call_tool(self, name: str, args: dict):
        async with streamablehttp_client(
                MCP_URL, headers={"Authorization": f"Bearer {self._access_token}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await s.call_tool(name, args)


def _open_session(access_token: str) -> _McpSession:
    """Шов для тестов: открытие/закрытие MCP-сессии инкапсулировано в _McpSession,
    вызывающий код просто зовёт call_tool сколько нужно раз."""
    return _McpSession(access_token)


PROJECTS_PAGE = 50

# Страховка от бесконечной пагинации: 20 страниц по 50 — тысяча проектов,
# запас на порядок. Так уже было: : list_projects не имеет параметра offset,
# он молча игнорировался, каждая страница возвращала одни и те же 50 проектов,
# и цикл `while True` крутился вечно — 1120 запросов к Мионике за 15 минут,
# поллер не обработал ни одного звонка. Курсор — единственный рабочий способ.
_MAX_PROJECT_PAGES = 20

import re as _re_cursor

_CURSOR_RE = _re_cursor.compile(r"cursor:\s*([A-Za-z0-9_=\-]+)")


def parse_projects_cursor(text: str) -> str | None:
    """Курсор следующей страницы из хвоста текстового ответа list_projects:
    `--- more results available (cursor: <opaque>) ---`. Нет строки — страниц больше нет."""
    m = _CURSOR_RE.search(text or "")
    return m.group(1) if m else None


def _result_text(result) -> str:
    return "\n".join(getattr(b, "text", "") or ""
                     for b in (getattr(result, "content", None) or [])
                     if getattr(b, "type", None) == "text")


async def fetch_projects(access_token: str) -> list[dict]:
    """Все проекты, а не первая страница. Сортировка по активности спасала до сих пор,
    но при >50 активных из поля зрения роутера начали бы выпадать живые проекты.

    Пагинация КУРСОРНАЯ (`after`), а не по смещению — у list_projects нет offset.
    Три независимые защиты от вечного цикла: курсор кончился, страница не принесла
    ни одного нового ключа, достигнут потолок страниц.
    """
    out: list[dict] = []
    seen: set[str] = set()
    cursor: str | None = None
    session = _open_session(access_token)
    for _ in range(_MAX_PROJECT_PAGES):
        args: dict = {"limit": PROJECTS_PAGE}
        if cursor:
            args["after"] = cursor
        res = await session.call_tool("list_projects", args)
        if getattr(res, "isError", False):
            raise RuntimeError(f"list_projects error: {getattr(res,'content',None)!r}")
        fresh = [p for p in _projects_from_result(res) if p.get("key") not in seen]
        out.extend(fresh)
        seen.update(p["key"] for p in fresh)
        cursor = parse_projects_cursor(_result_text(res))
        if not cursor or not fresh:
            return out
    _log.warning("fetch_projects: достигнут потолок в %d страниц, вернул %d проектов",
                 _MAX_PROJECT_PAGES, len(out))
    return out


import re as _re

_UUID_RE = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", _re.I)
_PID_CACHE: dict[str, str] = {}


def parse_project_uuid(text) -> str | None:
    for line in (text or "").splitlines():
        s = line.strip()
        if s.lower().startswith("id:"):
            return s.split(":", 1)[1].strip()
    return None


async def resolve_project_id(access_token: str, key_or_id: str) -> str:
    """Ключ проекта → UUID. batch_crud не резолвит некоторые ключи (key-resolver баг Mionika);
    пишем по UUID через get_project. Кэшируется. UUID пропускается как есть."""
    if not key_or_id or _UUID_RE.match(key_or_id):
        return key_or_id
    if key_or_id in _PID_CACHE:
        return _PID_CACHE[key_or_id]
    async with streamablehttp_client(MCP_URL, headers={"Authorization": f"Bearer {access_token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("get_project", {"idOrSlug": key_or_id})
            if getattr(res, "isError", False):
                raise RuntimeError(f"get_project error: {getattr(res,'content',None)!r}")
            txt = "".join(getattr(b, "text", "") or "" for b in (getattr(res, "content", None) or []))
    uid = parse_project_uuid(txt)
    if uid:
        _PID_CACHE[key_or_id] = uid
    return uid or key_or_id


async def write_proposed(access_token: str, project_id: str, ops: list[dict]) -> dict:
    pid = await resolve_project_id(access_token, project_id)   # batch_crud надёжнее по UUID
    async with streamablehttp_client(MCP_URL, headers={"Authorization": f"Bearer {access_token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("batch_crud", {"projectId": pid, "operations": ops})
            if getattr(res, "isError", False):
                raise RuntimeError(f"batch_crud error: {getattr(res,'content',None)!r}")
            return {"ok": True}


def _as_atom_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("atoms", "data", "items"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _rest_get_atoms(access_token, project_id, atom_type):
    """Fetch atoms of given type with pagination (200 per page)."""
    items, offset, PAGE = [], 0, 200
    with httpx.Client(timeout=30) as c:
        while True:
            r = c.get(f"{REST_BASE}/api/v1/atoms",
                      params={"type": atom_type, "limit": PAGE, "offset": offset},
                      headers={"Authorization": f"Bearer {access_token}", "X-Project-Id": project_id})
            r.raise_for_status()
            pg = _as_atom_list(r.json())
            items.extend(pg)
            if len(pg) < PAGE:
                break
            offset += PAGE
            if offset >= 5000:   # safety bound
                _log.warning("_rest_get_atoms: достигнут предел %d — результат может быть усечён "
                             "(проект %s)", offset, project_id)
                break
    return items


_CTX_TYPES = ("note", "decision", "task")


def _proposed_by_source(atoms, source):
    """Filter atoms to those matching source with disposition='proposed'; shape output."""
    out = []
    for a in atoms:
        md = a.get("metadata") or {}
        if md.get("source") == source and (md.get("disposition") or "").lower() == "proposed":
            out.append({"id": a.get("id"), "shortId": a.get("shortId"),
                        "title": a.get("title") or "", "type": a.get("type") or ""})
    return out


async def fetch_proposed_by_source(access_token, project_key_or_id, source):
    """Все proposed-атомы звонка (note/decision/task) в проекте — для «скип»."""
    pid = await resolve_project_id(access_token, project_key_or_id)
    items = []
    for t in _CTX_TYPES:
        atoms = await asyncio.to_thread(_rest_get_atoms, access_token, pid, t)
        items += _proposed_by_source(atoms, source)
    return items


