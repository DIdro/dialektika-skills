import asyncio
import pytest
from ops_ingest import review_actions as ra
from ops_ingest.store import Store
from ops_ingest.draft_schema import Draft


class _Cfg:
    mionika_token = "m"
    anthropic_key = "k"
    cp_base = "http://cp"
    cp_token = "t"


def _store(tmp_path, **over):
    s = Store(str(tmp_path / "s.db"))
    fields = dict(source="call:j1", summary="итоги", count=1, project="demo-bot",
                  confidence=0.9, tasks=[{"title": "написать тезисы"}],
                  atoms=[{"kind": "decision", "text": "решили переехать в веб"}],
                  status="awaiting")
    fields.update(over)
    s.upsert_queue_row("j1", **fields)
    return s


def _patch_mionika(monkeypatch, written):
    async def fake_resolve(tok, key):
        return f"uuid-{key}"

    async def fake_write(tok, project_id, ops):
        written.append({"project_id": project_id, "ops": ops})
        return {"ok": True}

    monkeypatch.setattr(ra.mio, "resolve_project_id", fake_resolve)
    monkeypatch.setattr(ra.mio, "write_proposed", fake_write)


def _patch_route(monkeypatch, result):
    async def fake_route(tok, pid, title, atoms, tasks, key, **kw):
        return result

    monkeypatch.setattr(ra.cu, "route_atoms", fake_route)


def _patch_fetch_projects(monkeypatch, projects):
    async def fake(tok):
        return projects

    monkeypatch.setattr(ra.mio, "fetch_projects", fake)


def _patch_split_atoms(monkeypatch, result):
    async def fake(atoms, tasks, projects, instruction, api_key, **kw):
        fake.calls.append({"atoms": atoms, "tasks": tasks, "projects": projects,
                           "instruction": instruction})
        return result

    fake.calls = []
    monkeypatch.setattr(ra.cu, "split_atoms", fake)
    return fake


def test_accept_writes_atoms_and_marks_row(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_route(monkeypatch, {"atom_nodes": [{"index": 0, "node": "MND-3"}],
                               "task_routes": [{"index": 0, "node": "MND-4"}],
                               "contradictions": [], "duplicates": []})
    s = _store(tmp_path)
    res = asyncio.run(ra.accept(_Cfg(), s, "j1"))
    assert res["written"] == 2                      # атом + задача
    assert written[0]["project_id"] == "uuid-demo-bot"
    assert written[0]["ops"][0]["metadata"]["disposition"] == "accepted"
    assert written[0]["ops"][0]["metadata"]["trustStatus"] == "pending_review"
    assert s.get_queue_row("j1")["status"] == "accepted"


def test_accept_stops_on_contradiction_until_forced(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_route(monkeypatch, {"atom_nodes": [{"index": 0, "node": "MND-3"}],
                               "task_routes": [], "duplicates": [],
                               "contradictions": [{"index": 0, "existing": "старое решение",
                                                   "note": "спорит с NOT-158"}]})
    s = _store(tmp_path)
    res = asyncio.run(ra.accept(_Cfg(), s, "j1"))
    assert res["needs_confirm"] is True
    assert written == []
    assert s.get_queue_row("j1")["status"] == "awaiting"
    res2 = asyncio.run(ra.accept(_Cfg(), s, "j1", force=True))
    assert res2["written"] >= 1
    assert s.get_queue_row("j1")["status"] == "accepted"


def test_accept_is_idempotent(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_route(monkeypatch, {"atom_nodes": [{"index": 0, "node": "MND-3"}],
                               "task_routes": [], "contradictions": [], "duplicates": []})
    s = _store(tmp_path)
    asyncio.run(ra.accept(_Cfg(), s, "j1"))
    res = asyncio.run(ra.accept(_Cfg(), s, "j1"))
    assert res["already"] == "accepted"
    assert len(written) == 1                        # второй раз не писали


def test_retarget_changes_project_without_llm(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("retarget не должен звать LLM")

    monkeypatch.setattr(ra.cu, "route_atoms", boom)
    s = _store(tmp_path)
    assert ra.retarget(s, "j1", "aaa-demo")["project"] == "aaa-demo"
    assert s.get_queue_row("j1")["project"] == "aaa-demo"


def test_reject_writes_nothing_and_marks_row(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    s = _store(tmp_path)
    assert asyncio.run(ra.reject(_Cfg(), s, "j1"))["rejected"] is True
    assert written == []
    assert s.get_queue_row("j1")["status"] == "rejected"


# ── единый гейт статуса: все пять действий работают только со строкой awaiting ──

def test_accept_on_split_row_returns_already_and_does_not_write(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_route(monkeypatch, {"atom_nodes": [{"index": 0, "node": "MND-3"}],
                               "task_routes": [], "contradictions": [], "duplicates": []})
    s = _store(tmp_path, status="split")
    res = asyncio.run(ra.accept(_Cfg(), s, "j1"))
    assert res == {"already": "split"}
    assert written == []
    assert s.get_queue_row("j1")["status"] == "split"


# ── split: разнесение по нескольким проектам ──────────────────────────────────

def test_split_creates_children_and_marks_parent_split_without_writing_to_graph(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"},
                                        {"key": "aaa-demo", "name": "Мир Здоровья"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"}],
              tasks=[{"title": "задача 1"}])
    fake_split = _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "demo-bot"},
                          {"index": 1, "project_key": "aaa-demo"}],
        "task_projects": [{"index": 0, "project_key": "demo-bot"}]})

    res = asyncio.run(ra.split(_Cfg(), s, "j1", "разнеси по темам"))

    assert res["projects"] == ["aaa-demo", "demo-bot"]
    assert res["dropped"] == 0
    assert written == []                                # split НИЧЕГО не пишет в граф

    # проекты переданы в split_atoms с именами, а не голыми ключами (иначе в
    # промпте будет "demo-bot — " с висящим тире)
    assert fake_split.calls[0]["projects"] == [{"key": "demo-bot", "name": "Demo Bot"},
                                               {"key": "aaa-demo", "name": "Мир Здоровья"}]

    child = s.get_queue_row("j1#demo-bot")
    assert child["status"] == "awaiting"
    assert child["source"] == "call:j1"                 # источник наследуется от родителя
    assert child["summary"] == "итоги"
    assert child["project"] == "demo-bot"
    assert len(child["atoms"]) == 1 and child["atoms"][0]["text"] == "атом А"
    assert len(child["tasks"]) == 1 and child["tasks"][0]["title"] == "задача 1"

    other = s.get_queue_row("j1#aaa-demo")
    assert len(other["atoms"]) == 1 and other["atoms"][0]["text"] == "атом Б"
    assert other["tasks"] == []

    parent = s.get_queue_row("j1")
    assert parent["status"] == "split"                  # НЕ rejected — см. бриф


def test_split_logs_and_counts_dropped_atoms(tmp_path, monkeypatch, caplog):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"},
                               {"kind": "note", "text": "атом В"}],
              tasks=[])
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "demo-bot"},
                          {"index": 1, "project_key": "неизвестный-проект"}],
        # индекс 2 модель вообще не упомянула
        "task_projects": []})

    with caplog.at_level("WARNING"):
        res = asyncio.run(ra.split(_Cfg(), s, "j1", "разнеси"))

    assert res["dropped"] == 2                          # неизвестный проект + неупомянутый атом
    assert written == []
    assert len(caplog.records) >= 2
    # уцелевшее ушло в один проект — ветку не плодим, у звонка сменился проект,
    # а отброшенное модель не отнесла никуда и в строке не остаётся
    assert res["retargeted"] is True
    assert s.get_queue_row("j1#demo-bot") is None
    row = s.get_queue_row("j1")
    assert row["status"] == "awaiting" and row["project"] == "demo-bot"
    assert [a["text"] for a in row["atoms"]] == ["атом А"]


# ── reparse: полный переразбор ────────────────────────────────────────────────

def _patch_classify_and_extract(monkeypatch, draft, atoms):
    calls = {}

    async def fake_classify(text, projects, api_key, extra_instruction=None, **kw):
        calls["text"] = text
        calls["extra_instruction"] = extra_instruction
        return draft

    async def fake_extract(text, api_key, **kw):
        return {"atoms": atoms, "recency": {}}

    monkeypatch.setattr(ra, "classify", fake_classify)
    monkeypatch.setattr(ra.cu, "extract_atoms", fake_extract)
    return calls


def test_reparse_does_not_write_to_graph_and_returns_to_awaiting(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"}])
    s = _store(tmp_path)
    s.mark_processed("j1", "исходный транскрипт")
    draft = Draft.model_validate({"summary": "новая сводка",
                                  "primary_project": {"key": "demo-bot", "confidence": 0.9,
                                                       "rationale": "r"}})
    calls = _patch_classify_and_extract(monkeypatch, draft,
                                        [{"kind": "note", "text": "новый атом"}])

    res = asyncio.run(ra.reparse(_Cfg(), s, "j1", "смотри внимательнее на суммы"))

    assert res["atoms"] == 1
    assert written == []                                # reparse НИЧЕГО не пишет в граф
    assert calls["extra_instruction"] == "смотри внимательнее на суммы"
    row = s.get_queue_row("j1")
    assert row["status"] == "awaiting"
    assert row["summary"] == "новая сводка"


def test_reparse_unknown_job_raises_lookup_error(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    with pytest.raises(LookupError):
        asyncio.run(ra.reparse(_Cfg(), s, "ghost", "prompt"))


def test_reparse_returns_row_to_awaiting_on_error(tmp_path, monkeypatch):
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"}])
    s = _store(tmp_path)
    s.mark_processed("j1", "транскрипт")

    async def boom(*a, **kw):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(ra, "classify", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(ra.reparse(_Cfg(), s, "j1", "prompt"))
    assert s.get_queue_row("j1")["status"] == "awaiting"


def test_reparse_on_child_row_is_forbidden_and_touches_nothing(tmp_path, monkeypatch):
    """Переразбор потомка тихо перезаписал бы его atoms и project содержимым
    ВСЕГО звонка (у потомка нет своей транскрипт-истории), а сосед-потомок
    хранил бы свою долю — приняв обоих, владелец задвоил бы материал в графе.
    Правильный путь исправить неудачное разнесение — повторный split на
    родителе, поэтому reparse на потомке просто отказывает, не трогая строку."""
    written = []
    _patch_mionika(monkeypatch, written)

    def boom(*a, **kw):
        raise AssertionError("reparse потомка не должен звать ни LLM, ни call-pipeline")

    monkeypatch.setattr(ra.mio, "fetch_projects", boom)
    monkeypatch.setattr(ra.cp, "get_transcript_text", boom)
    monkeypatch.setattr(ra, "classify", boom)
    monkeypatch.setattr(ra.cu, "extract_atoms", boom)

    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1#demo-bot", source="call:j1", summary="s", count=1,
                       project="demo-bot", confidence=None, tasks=[], atoms=[],
                       status="awaiting")
    before = s.get_queue_row("j1#demo-bot")

    res = asyncio.run(ra.reparse(_Cfg(), s, "j1#demo-bot", "prompt"))

    assert res == {"error": "reparse_on_child"}
    assert written == []
    after = s.get_queue_row("j1#demo-bot")
    assert after == before                              # ни статус, ни поля не тронуты


def test_split_on_child_row_is_forbidden_and_touches_nothing(tmp_path, monkeypatch):
    """Разнесение потомка обходит защиту от двойной записи: проверка принятых
    потомков смотрит только на ПРЯМЫХ потомков, а осиротевшими помечаются только
    ждущие — значит разнесённый потомок вместе со своим поддеревом переживает
    переразнесение родителя, и его потомков можно принять рядом с новой
    раскладкой. Двухуровневое разнесение запрещено целиком."""
    written = []
    _patch_mionika(monkeypatch, written)

    def boom(*a, **kw):
        raise AssertionError("split потомка не должен звать ни LLM, ни Мионику")

    monkeypatch.setattr(ra.mio, "fetch_projects", boom)
    monkeypatch.setattr(ra.cu, "split_atoms", boom)

    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1#demo-bot", source="call:j1", summary="s", count=1,
                       project="demo-bot", confidence=None, tasks=[],
                       atoms=[{"kind": "note", "text": "а"}], status="awaiting")
    before = s.get_queue_row("j1#demo-bot")

    res = asyncio.run(ra.split(_Cfg(), s, "j1#demo-bot", "разнеси ещё раз"))

    assert res == {"error": "split_on_child"}
    assert written == []
    assert s.get_queue_row("j1#demo-bot") == before      # строка не тронута
    # и никакого внука не появилось
    assert [r["job_id"] for r in s.list_queue()] == ["j1#demo-bot"]


# ── повторное разнесение (split на строке в статусе split) ────────────────────

def test_resplit_on_split_row_replaces_children(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"},
                                        {"key": "aaa-demo", "name": "Мир Здоровья"},
                                        {"key": "aab-demo", "name": "ДИИП"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"}],
              tasks=[])
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "demo-bot"},
                          {"index": 1, "project_key": "aaa-demo"}],
        "task_projects": []})
    res1 = asyncio.run(ra.split(_Cfg(), s, "j1", "первая раскладка"))
    assert res1["projects"] == ["aaa-demo", "demo-bot"]
    assert s.get_queue_row("j1")["status"] == "split"

    # владелец недоволен раскладкой и просит переиграть по другой инструкции —
    # aaa-demo в новой раскладке больше нет, вместо него aab-demo
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "aab-demo"},
                          {"index": 1, "project_key": "demo-bot"}],
        "task_projects": []})
    res2 = asyncio.run(ra.split(_Cfg(), s, "j1", "вторая раскладка"))

    assert res2["projects"] == ["aab-demo", "demo-bot"]
    assert written == []                                # split по-прежнему ничего не пишет
    assert s.get_queue_row("j1")["status"] == "split"
    child = s.get_queue_row("j1#aab-demo")
    assert child["status"] == "awaiting"
    assert [a["text"] for a in child["atoms"]] == ["атом А"]
    assert s.get_queue_row("j1#demo-bot")["atoms"][0]["text"] == "атом Б"


def test_resplit_forbidden_when_a_child_already_accepted(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"},
                                        {"key": "aaa-demo", "name": "Мир Здоровья"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"}],
              tasks=[])
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "demo-bot"},
                          {"index": 1, "project_key": "aaa-demo"}],
        "task_projects": []})
    asyncio.run(ra.split(_Cfg(), s, "j1", "первая раскладка"))

    # владелец принимает одного из потомков
    _patch_route(monkeypatch, {"atom_nodes": [{"index": 0, "node": "MND-3"}],
                               "task_routes": [], "contradictions": [], "duplicates": []})
    asyncio.run(ra.accept(_Cfg(), s, "j1#demo-bot"))
    assert s.get_queue_row("j1#demo-bot")["status"] == "accepted"
    written.clear()

    res = asyncio.run(ra.split(_Cfg(), s, "j1", "переиграть ещё раз"))

    assert res == {"error": "split_has_accepted_children"}
    assert written == []
    # ничего не тронуто: принятый потомок остался принятым, второй — как был
    assert s.get_queue_row("j1#demo-bot")["status"] == "accepted"
    assert s.get_queue_row("j1#aaa-demo")["status"] == "awaiting"


def test_resplit_does_not_leave_orphaned_children(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"},
                                        {"key": "aaa-demo", "name": "Мир Здоровья"},
                                        {"key": "aab-demo", "name": "ДИИП"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"}],
              tasks=[])
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "demo-bot"},
                          {"index": 1, "project_key": "aaa-demo"}],
        "task_projects": []})
    asyncio.run(ra.split(_Cfg(), s, "j1", "первая раскладка"))
    assert s.get_queue_row("j1#demo-bot") is not None
    assert s.get_queue_row("j1#aaa-demo") is not None

    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "aab-demo"},
                          {"index": 1, "project_key": "demo-bot"}],
        "task_projects": []})
    asyncio.run(ra.split(_Cfg(), s, "j1", "вторая раскладка"))

    all_children = [r for r in s.list_queue() if r["job_id"].startswith("j1#")]
    awaiting_children = [r for r in all_children if r["status"] == "awaiting"]
    assert sorted(r["job_id"] for r in awaiting_children) == ["j1#aab-demo", "j1#demo-bot"]
    # потомок старой раскладки не потерян бесследно, но и не висит в awaiting —
    # он закрыт (rejected), реально осиротевших строк нет
    assert s.get_queue_row("j1#aaa-demo")["status"] != "awaiting"


# ── разнесение в один проект = смена проекта, без ветки ───────────────────────
# Так уже было: : модель унесла все 31 атом звонка в один проект — в очереди
# появились родитель «разнесено» и ветка с теми же 31 атомом, это читалось как дубль.

def test_split_into_single_project_retargets_instead_of_branching(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"},
                                        {"key": "aaa-demo", "name": "Мир Здоровья"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"}],
              tasks=[{"title": "задача 1"}])
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "aaa-demo"},
                          {"index": 1, "project_key": "aaa-demo"}],
        "task_projects": [{"index": 0, "project_key": "aaa-demo"}]})

    res = asyncio.run(ra.split(_Cfg(), s, "j1", "проверь дубли и разнеси"))

    assert res == {"projects": ["aaa-demo"], "dropped": 0, "retargeted": True}
    assert written == []
    assert [r["job_id"] for r in s.list_queue()] == ["j1"]      # ни одной ветки
    row = s.get_queue_row("j1")
    assert row["status"] == "awaiting"
    assert row["project"] == "aaa-demo"
    assert row["confidence"] is None                  # уверенность классификатора больше не про этот проект
    assert len(row["atoms"]) == 2 and len(row["tasks"]) == 1 and row["count"] == 3
    assert row["reparse_prompt"] == "проверь дубли и разнеси"


def test_resplit_into_single_project_folds_back_into_parent(tmp_path, monkeypatch):
    written = []
    _patch_mionika(monkeypatch, written)
    _patch_fetch_projects(monkeypatch, [{"key": "demo-bot", "name": "Demo Bot"},
                                        {"key": "aaa-demo", "name": "Мир Здоровья"},
                                        {"key": "aab-demo", "name": "ДИИП"}])
    s = _store(tmp_path, atoms=[{"kind": "decision", "text": "атом А"},
                               {"kind": "note", "text": "атом Б"}],
              tasks=[])
    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "demo-bot"},
                          {"index": 1, "project_key": "aaa-demo"}],
        "task_projects": []})
    asyncio.run(ra.split(_Cfg(), s, "j1", "первая раскладка"))

    _patch_split_atoms(monkeypatch, {
        "atom_projects": [{"index": 0, "project_key": "aab-demo"},
                          {"index": 1, "project_key": "aab-demo"}],
        "task_projects": []})
    res = asyncio.run(ra.split(_Cfg(), s, "j1", "всё это aab-demo"))

    assert res["retargeted"] is True and res["projects"] == ["aab-demo"]
    assert written == []
    row = s.get_queue_row("j1")
    assert row["status"] == "awaiting" and row["project"] == "aab-demo"
    assert len(row["atoms"]) == 2
    assert s.get_queue_row("j1#aab-demo") is None
    children = [r for r in s.list_queue() if r["job_id"].startswith("j1#")]
    assert children and all(r["status"] != "awaiting" for r in children)
