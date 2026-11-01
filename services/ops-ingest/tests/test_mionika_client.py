import asyncio

from ops_ingest import mionika_client as mio
from ops_ingest.mionika_client import parse_projects_text, _proposed_by_source


def _result(text: str):
    return type("R", (), {"content": [type("C", (), {"type": "text", "text": text})()]})()


class _FakeSession:
    """Изображает MCP-сессию list_projects: КУРСОРНАЯ пагинация через `after`.

    Именно так устроен реальный инструмент — параметра `offset` у него нет.
    Прежняя версия этой заглушки уважала `offset`, поэтому тест был зелёным,
    а в проде цикл не завершался никогда (так уже было: : 1120 запросов
    к Мионике за 15 минут, ни одного разобранного звонка).
    """

    def __init__(self, total, honour_cursor=True, tail_cursor=False):
        self.total = total
        self.honour_cursor = honour_cursor   # False — сервер игнорирует курсор
        self.tail_cursor = tail_cursor       # True — сервер всегда обещает ещё страницу
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append(args)
        limit = args["limit"]
        start = int(args["after"]) if (self.honour_cursor and args.get("after")) else 0
        keys = [f"p{i}" for i in range(start, min(start + limit, self.total))]
        lines = [f"{k} | Проект {k}" for k in keys]   # формат реального list_projects
        nxt = start + len(keys)
        if self.tail_cursor or nxt < self.total:
            lines.append(f"--- more results available (cursor: {nxt}) ---")
        return _result("\n".join(lines))


def test_fetch_projects_pages_past_fifty(monkeypatch):
    fake = _FakeSession(total=72)
    monkeypatch.setattr(mio, "_open_session", lambda tok: fake)
    got = asyncio.run(mio.fetch_projects("tok"))
    assert len(got) == 72
    assert len(fake.calls) >= 2
    # Вторая страница обязана запрашиваться курсором, а не смещением.
    assert "after" in fake.calls[1]
    assert "offset" not in fake.calls[1]


def test_fetch_projects_stops_when_server_ignores_cursor(monkeypatch):
    """Сервер отдаёт одну и ту же страницу и всегда обещает продолжение.

    Ровно то, что случилось в проде 06.09. Цикл обязан остановиться на второй
    странице: новых ключей она не принесла.
    """
    fake = _FakeSession(total=200, honour_cursor=False, tail_cursor=True)
    monkeypatch.setattr(mio, "_open_session", lambda tok: fake)
    got = asyncio.run(mio.fetch_projects("tok"))
    assert len(got) == 50           # только уникальные ключи
    assert len(fake.calls) == 2     # вторая страница без новизны — выходим


def test_fetch_projects_page_cap_is_the_last_line_of_defence(monkeypatch):
    """Даже если сервер каждый раз отдаёт НОВЫЕ ключи и вечно обещает ещё,
    цикл упирается в потолок страниц, а не крутится бесконечно."""

    class _Endless:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            n = len(self.calls)
            self.calls.append(args)
            lines = [f"p{n}_{i} | Проект" for i in range(args["limit"])]
            lines.append("--- more results available (cursor: next) ---")
            return _result("\n".join(lines))

    fake = _Endless()
    monkeypatch.setattr(mio, "_open_session", lambda tok: fake)
    got = asyncio.run(mio.fetch_projects("tok"))
    assert len(fake.calls) == mio._MAX_PROJECT_PAGES
    assert len(got) == mio._MAX_PROJECT_PAGES * mio.PROJECTS_PAGE


def test_parse_projects_cursor():
    from ops_ingest.mionika_client import parse_projects_cursor
    tail = "a | A\n--- more results available (cursor: MjAyNi0wOS0wNlQx_ab-CD=) ---"
    assert parse_projects_cursor(tail) == "MjAyNi0wOS0wNlQx_ab-CD="
    assert parse_projects_cursor("a | A\nb | B") is None


def test_cursor_line_is_not_parsed_as_project():
    """Хвост с курсором не должен превратиться в проект с мусорным ключом."""
    text = "a | A | owner | today\n--- more results available (cursor: XYZ) ---"
    assert parse_projects_text(text) == [{"key": "a", "name": "A"}]


def test_proposed_by_source_filters_and_shapes():
    atoms = [
        {"id": "a1", "shortId": "TSK-1", "title": "t1", "type": "task",
         "metadata": {"source": "call:j1", "disposition": "proposed"}},
        {"id": "a2", "shortId": "NTE-2", "title": "n1", "type": "note",
         "metadata": {"source": "call:j1", "disposition": ""}},        # уже принят
        {"id": "a3", "shortId": "NTE-3", "title": "n2", "type": "note",
         "metadata": {"source": "call:OTHER", "disposition": "proposed"}},
    ]
    out = _proposed_by_source(atoms, "call:j1")
    assert out == [{"id": "a1", "shortId": "TSK-1", "title": "t1", "type": "task"}]


def test_parse_projects_text():
    text = "sales-pipeline | Воронка продаж | owner | today\norbita-live | Орбита | owner | today\n"
    out = parse_projects_text(text)
    assert out == [{"key": "sales-pipeline", "name": "Воронка продаж"},
                   {"key": "orbita-live", "name": "Орбита"}]


def test_parse_project_uuid():
    from ops_ingest.mionika_client import parse_project_uuid
    txt = "demo-bot | Demo Bot\nid: 00000000-0000-7000-8000-000000000001\ncounters: 110 atoms"
    assert parse_project_uuid(txt) == "00000000-0000-7000-8000-000000000001"
    assert parse_project_uuid("no id here") is None
