from ops_ingest.draft_schema import (Draft, CommitmentItem, ProjectGuess,
                                     Uncertainty, alternatives_of, uncertainty_text)


def test_commitment_item_board_fields_optional():
    c = CommitmentItem(what="прислать КП")
    assert c.activity_type is None and c.owner_suggestion is None


def test_commitment_item_accepts_board_fields():
    c = CommitmentItem(what="решить про базу знаний", activity_type="стратегия", owner_suggestion="owner")
    assert c.activity_type == "стратегия"


def test_draft_minimal_defaults_empty_commitments():
    d = Draft(summary="звонок про МЗ")
    assert d.commitments.by_owner == [] and d.primary_project is None


def test_owner_identified_defaults_to_none():
    d = Draft(summary="итоги")
    assert d.owner_identified is None


def test_owner_identified_roundtrip():
    d = Draft(summary="итоги", owner_identified=False)
    assert d.model_dump()["owner_identified"] is False


# ── альтернативы и неуверенность классификатора → строка очереди ─────────────

def test_alternatives_of_empty_when_model_named_none():
    assert alternatives_of(Draft(summary="итоги")) == []


def test_alternatives_of_carries_key_confidence_and_rationale():
    d = Draft(summary="итоги",
              primary_project=ProjectGuess(key="demo-bot", confidence=0.55, rationale="про доклад"),
              alternative_projects=[ProjectGuess(key="demo-crm", confidence=0.30, rationale="упомянут МФТИ")])
    assert alternatives_of(d) == [{"key": "demo-crm", "confidence": 0.30,
                                   "rationale": "упомянут МФТИ"}]


def test_uncertainty_text_empty_when_model_was_sure():
    assert uncertainty_text(Draft(summary="итоги")) == ""


def test_uncertainty_text_keeps_options():
    """Варианты, которые модель сама назвала, приклеиваются к тексту, а не теряются."""
    d = Draft(summary="итоги",
              uncertainty=Uncertainty(description="непонятно, чей это проект",
                                      options=["demo-bot", "demo-crm"]))
    assert uncertainty_text(d) == "непонятно, чей это проект; варианты: demo-bot, demo-crm"


def test_uncertainty_text_without_options_is_just_description():
    d = Draft(summary="итоги", uncertainty=Uncertainty(description="дата не названа"))
    assert uncertainty_text(d) == "дата не названа"


def test_stringified_nested_fields_are_parsed():
    """Модель изредка отдаёт вложенное поле строкой с JSON вместо объекта.

    Замер на доборе 06.09: 2 отказа из 78 звонков. Без разбора такой драфт
    отвергался целиком и звонок оставался неразобранным.
    """
    d = Draft(
        summary="итоги",
        commitments='{"by_owner": [{"what": "прислать смету"}]}',
        uncertainty='{"description": "проект не назван", "options": ["iskra"]}',
        primary_project='{"key": "iskra", "confidence": 0.8, "rationale": "по содержанию"}',
        alternative_projects='[{"key": "taif", "confidence": 0.2, "rationale": "слабо"}]',
    )
    assert d.commitments.by_owner[0].what == "прислать смету"
    assert d.uncertainty.description == "проект не назван"
    assert d.primary_project.key == "iskra"
    assert d.alternative_projects[0].key == "taif"


def test_stringified_fields_with_trailing_garbage_are_parsed():
    """Строка с JSON и лишней скобкой в хвосте — переразбор 28.10 упал с 500
    на `commitments='{...}]'`: json.loads отвергал хвост, строка уходила в
    валидацию как есть. Ведущий объект целый — его и берём."""
    d = Draft(
        summary="итоги",
        commitments='{"by_owner": [{"what": "прислать смету"}]}]',
        uncertainty='{"description": "проект не назван", "options": ["iskra"]}]',
    )
    assert d.commitments.by_owner[0].what == "прислать смету"
    assert d.uncertainty.options == ["iskra"]


def test_objects_still_accepted_as_objects():
    """Обычный путь не сломан: объекты по-прежнему принимаются как объекты."""
    d = Draft(summary="итоги", uncertainty={"description": "неясно"})
    assert d.uncertainty.description == "неясно"


def test_unparseable_string_falls_through_to_validation():
    """Строка, которая не разбирается как JSON, не подменяется молча —
    валидация должна ругаться по существу."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Draft(summary="итоги", uncertainty="просто текст, не JSON")
