import pytest

from app.lib.context_providers.file import FileProvider


@pytest.mark.asyncio
async def test_reads_projects(tmp_path, monkeypatch):
    f = tmp_path / "projects.md"
    f.write_text(
        "# Проекты\n\nbot — Бот заказов — пилот на 2 точках\ncrm — CRM\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPELINE_PROJECTS_FILE", str(f))
    p = FileProvider()
    assert p.is_available({})
    assert (await p.fetch_context("", {}))["projects"] == [
        {"key": "bot", "name": "Бот заказов", "about": "пилот на 2 точках"},
        {"key": "crm", "name": "CRM", "about": ""},
    ]


@pytest.mark.asyncio
async def test_missing_file_reports_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_PROJECTS_FILE", str(tmp_path / "nope.md"))
    ctx = await FileProvider().fetch_context("", {})
    assert ctx["projects"] == [] and "error" in ctx


def test_unavailable_without_env(monkeypatch):
    monkeypatch.delenv("PIPELINE_PROJECTS_FILE", raising=False)
    assert not FileProvider().is_available({})
