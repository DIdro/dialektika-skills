import asyncio
import json
from types import SimpleNamespace

from ops_ingest import file_backend, review_actions as ra
from ops_ingest.store import Store


def test_projects_from_file(tmp_path):
    f = tmp_path / "projects.md"
    f.write_text("# Проекты\n\nbot — Бот заказов — пилот\ncrm — CRM\n", encoding="utf-8")
    got = asyncio.run(file_backend.fetch_projects(SimpleNamespace(projects_file=str(f))))
    assert got == [{"key": "bot", "name": "Бот заказов"}, {"key": "crm", "name": "CRM"}]


def test_accept_writes_files_without_mionika(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.upsert_queue_row("j1", source="call:j1", summary="итоги", project="bot",
                           atoms=[{"kind": "decision", "text": "решили"}],
                           tasks=[{"title": "прислать КП", "owner": "owner"}],
                           status="awaiting")
    cfg = SimpleNamespace(accepted_dir=str(tmp_path / "out"))
    assert asyncio.run(ra.accept(cfg, store, "j1"))["written"] == 2
    assert store.get_queue_row("j1")["status"] == "accepted"
    data = json.loads((tmp_path / "out" / "bot-j1.json").read_text(encoding="utf-8"))
    assert data["atoms"][0]["text"] == "решили"
    assert "прислать КП" in (tmp_path / "out" / "bot-j1.md").read_text(encoding="utf-8")
