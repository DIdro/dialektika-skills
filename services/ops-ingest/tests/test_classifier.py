import asyncio
from types import SimpleNamespace

from ops_ingest.classifier import extract_draft, classify
from ops_ingest import classifier as clf


def test_extract_draft_validates():
    d = extract_draft({"summary": "звонок", "commitments": {
        "by_owner": [{"what": "прислать КП", "activity_type": "продажи", "owner_suggestion": "owner"}]}})
    assert d.commitments.by_owner[0].what == "прислать КП"


class _Block:
    type = "tool_use"
    name = "submit_draft"

    def __init__(self, data):
        self.input = data


def test_classify_extra_instruction_lands_in_user_message(monkeypatch):
    """review_actions.reparse дописывает инструкцию владельца перед вызовом
    инструмента — проверяем, что она реально попадает в текст пользовательского
    сообщения, а не теряется."""
    captured = {}

    class _Messages:
        async def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(content=[_Block({"summary": "s"})])

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    monkeypatch.setattr(clf, "AsyncAnthropic", _Client)
    asyncio.run(classify("транскрипт", [{"key": "demo-bot", "name": "Demo Bot"}], "key",
                         extra_instruction="обрати внимание на сроки"))
    user_text = captured["messages"][0]["content"]
    assert "обрати внимание на сроки" in user_text
    assert "Вызови submit_draft." in user_text


def test_classify_without_extra_instruction_is_unchanged(monkeypatch):
    """Существующие вызовы без extra_instruction обязаны продолжать работать
    так же, как раньше."""
    captured = {}

    class _Messages:
        async def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(content=[_Block({"summary": "s"})])

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    monkeypatch.setattr(clf, "AsyncAnthropic", _Client)
    asyncio.run(classify("транскрипт", [{"key": "demo-bot", "name": "Demo Bot"}], "key"))
    user_text = captured["messages"][0]["content"]
    assert user_text.endswith("Вызови submit_draft.")
