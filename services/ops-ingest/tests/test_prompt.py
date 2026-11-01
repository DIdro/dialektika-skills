from ops_ingest.prompt import build_messages, SYSTEM_INSTRUCTIONS, DRAFT_TOOL_NAME


def test_prompt_has_no_hardcoded_project_names():
    for slug in ("demo-a", "demo-b", "demo-c", "demo-d"):
        assert slug not in SYSTEM_INSTRUCTIONS


def test_prompt_does_not_claim_transcript_is_undiarized():
    """Инструкция «работаешь БЕЗ диаризации» писалась под mode=short и перебивала
    разметку в mode=call, где спикеры размечены. Из-за неё модель атрибутировала
    обещания по контексту и слипляла разных людей в одного."""
    assert "БЕЗ диаризации" not in SYSTEM_INSTRUCTIONS


def test_prompt_explains_speaker_label_format():
    """Модель должна узнавать реальный формат меток, который теперь приходит."""
    assert "[SPEAKER_" in SYSTEM_INSTRUCTIONS


def test_prompt_requires_identifying_owner_among_speakers():
    """Метки анонимны: SPEAKER_00 сам по себе не говорит, кто из них владелец.
    Опознание делается один раз на звонок, а не переугадывается в каждой фразе."""
    assert "кто из спикеров владелец" in SYSTEM_INSTRUCTIONS


def test_build_messages_injects_projects_and_transcript():
    payload = build_messages(
        projects=[{"key": "aaa-demo", "name": "Мир Здоровья"}],
        transcript_text="владелец: я пришлю КП завтра.",
    )
    sys_text = " ".join(b["text"] for b in payload["system"])
    assert "aaa-demo" in sys_text          # снапшот вложен сервисом
    assert "я пришлю КП" in payload["messages"][0]["content"]
    assert payload["tools"][0]["name"] == DRAFT_TOOL_NAME
    assert payload["tool_choice"] == {"type": "tool", "name": DRAFT_TOOL_NAME}


def test_prompt_requires_owner_identified():
    payload = build_messages(
        projects=[{"key": "demo-bot", "name": "Demo Bot"}],
        transcript_text="транскрипт",
    )
    system_text = " ".join(b["text"] for b in payload["system"])
    assert "owner_identified" in system_text
    schema = payload["tools"][0]["input_schema"]["properties"]
    assert "owner_identified" in schema
