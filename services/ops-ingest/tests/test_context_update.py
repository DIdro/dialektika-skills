import asyncio
from types import SimpleNamespace

from ops_ingest.context_update import extract_updates, is_stale_by_date, decide_stale, tasks_to_ops
from ops_ingest.context_update import atoms_to_ops
from ops_ingest import context_update as cu


class _Block:
    type = "tool_use"

    def __init__(self, name, data):
        self.name = name
        self.input = data


def _fake_anthropic(monkeypatch, name, data):
    class _Messages:
        async def create(self, **kw):
            return SimpleNamespace(content=[_Block(name, data)])

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    monkeypatch.setattr(cu, "AsyncAnthropic", _Client)


def test_decide_stale_recorded_at_authoritative():
    # recorded_at есть → решает он, даже если LLM сказал «не старьё»
    assert decide_stale("2026-03-01", None, False, "2026-06-19") is True
    assert decide_stale("2026-06-18", "2026-01-01", True, "2026-06-19") is False  # recorded свежий → не старьё
    # recorded_at нет → оценка по тексту
    assert decide_stale(None, None, True, "2026-06-19") is True
    assert decide_stale(None, "2026-02-28", False, "2026-06-19") is True
    assert decide_stale(None, None, False, "2026-06-19") is False


def test_is_stale_by_date():
    assert is_stale_by_date("2026-03-31", "2026-06-18") is True       # >21 дней — старьё
    assert is_stale_by_date("2026-06-15", "2026-06-18") is False      # свежее
    assert is_stale_by_date(None, "2026-06-18") is False              # нет даты — не скипаем
    assert is_stale_by_date("мусор", "2026-06-18") is False           # не распарсилось — не скипаем
    assert is_stale_by_date("2026-06-18", None) is False              # нет today — не скипаем


def test_extract_updates():
    d = extract_updates({"updates": [{"node": "MND-8", "kind": "decision", "text": "выложить ТЗ"}],
                         "recency": {"is_stale": False}})
    assert d["updates"][0]["node"] == "MND-8" and d["recency"]["is_stale"] is False


def test_tasks_to_ops_routes_and_orphans():
    tasks = [{"title": "Прислать презентацию", "owner": "owner", "activity_type": "продажи",
              "deadline": "2026-08-05"},
             {"title": "Подумать про вебинар", "owner": "owner", "soft": True}]
    routes = [{"index": 0, "node": "MND-3"}, {"index": 1, "node": "NONE"}]
    ops = tasks_to_ops(tasks, routes, "j1")
    assert len(ops) == 2
    a, b = ops
    assert a["ref"] == "task_j1_0" and a["type"] == "task"
    assert a["metadata"]["taskStatus"] == "backlog"
    assert a["metadata"]["disposition"] == "accepted"
    assert a["metadata"]["source"] == "call:j1"
    assert a["metadata"]["deadline"] == "2026-08-05"
    assert "trustStatus" not in a["metadata"]          # trust-workflow не для task
    assert a["links"] == [{"type": "contains", "from": "MND-3"}]
    assert b["title"].startswith("[мягкое] ")
    assert "links" not in b                            # NONE → сирота, но видна в review


def test_tasks_to_ops_missing_route_is_none():
    ops = tasks_to_ops([{"title": "x"}], [], "j1")
    assert len(ops) == 1 and "links" not in ops[0]


def test_extract_updates_carries_task_routes():
    data = extract_updates({"updates": [], "recency": {"is_stale": False},
                            "task_routes": [{"index": 0, "node": "MND-1"}]})
    assert data["task_routes"] == [{"index": 0, "node": "MND-1"}]


def test_atoms_to_ops_builds_accepted_atoms_with_links():
    atoms = [{"kind": "decision", "text": "решили переехать в веб"},
             {"kind": "note", "text": "второй атом"}]
    nodes = [{"index": 0, "node": "MND-3"}, {"index": 1, "node": "NONE"}]
    ops = atoms_to_ops(atoms, nodes, duplicates=[], job_id="j1")
    assert len(ops) == 2
    assert ops[0]["type"] == "decision"
    assert ops[0]["metadata"]["disposition"] == "accepted"
    assert ops[0]["metadata"]["trustStatus"] == "pending_review"
    assert ops[0]["metadata"]["source"] == "call:j1"
    assert ops[0]["links"] == [{"type": "contains", "from": "MND-3"}]
    assert ops[0]["ref"] == "ctx_j1_0"
    assert "links" not in ops[1]          # NONE → сирота, но задачу/атом не теряем


def test_atoms_to_ops_drops_duplicates():
    atoms = [{"kind": "note", "text": "дубль"}]
    ops = atoms_to_ops(atoms, [{"index": 0, "node": "MND-3"}], duplicates=[0], job_id="j1")
    assert ops == []


def test_extract_atoms_returns_atoms_and_recency(monkeypatch):
    _fake_anthropic(monkeypatch, "submit_atoms", {
        "atoms": [{"kind": "decision", "text": "решили переехать в веб"}],
        "recency": {"is_stale": False, "latest_date": "2026-09-01"}})
    got = asyncio.run(cu.extract_atoms("транскрипт", "key", today="2026-09-06"))
    assert got["atoms"] == [{"kind": "decision", "text": "решили переехать в веб"}]
    assert got["recency"]["latest_date"] == "2026-09-01"
