from ops_ingest.gate import evaluate
from ops_ingest.draft_schema import Draft, Commitments, CommitmentItem, ProjectGuess

FACTS_OK = {"diarized": True, "speaker_count": 2, "unknown_share": 0.05}


def _draft(**kw):
    base = dict(summary="итоги",
                primary_project=ProjectGuess(key="demo-bot", confidence=0.9, rationale="r"),
                owner_identified=True)
    base.update(kw)
    return Draft(**base)


def test_clean_call_has_no_flags():
    assert evaluate(_draft(), FACTS_OK, "zoom") == []


def test_low_confidence_raises_project_unclear():
    d = _draft(primary_project=ProjectGuess(key="demo-bot", confidence=0.4, rationale="r"))
    assert "project_unclear" in evaluate(d, FACTS_OK, "zoom")


def test_missing_project_raises_project_unclear():
    assert "project_unclear" in evaluate(_draft(primary_project=None), FACTS_OK, "zoom")


def test_commitment_with_deadline_raises_stakes():
    d = _draft(commitments=Commitments(
        by_owner=[CommitmentItem(what="пришлю КП", deadline="2026-09-10")]))
    assert "commitment_with_stakes" in evaluate(d, FACTS_OK, "zoom")


def test_commitment_with_money_raises_stakes():
    d = _draft(commitments=Commitments(
        by_owner=[CommitmentItem(what="сделаем за 300 000 руб")]))
    assert "commitment_with_stakes" in evaluate(d, FACTS_OK, "zoom")


def test_commitment_with_short_money_form_raises_stakes():
    d = _draft(commitments=Commitments(
        by_owner=[CommitmentItem(what="закину 50к")]))
    assert "commitment_with_stakes" in evaluate(d, FACTS_OK, "zoom")


def test_owner_not_identified_raises_attribution():
    assert "attribution_unclear" in evaluate(_draft(owner_identified=False), FACTS_OK, "zoom")


def test_high_unknown_share_raises_attribution():
    facts = {"diarized": True, "speaker_count": 2, "unknown_share": 0.5}
    assert "attribution_unclear" in evaluate(_draft(), facts, "zoom")


def test_owner_identified_none_alone_does_not_raise_attribution():
    """None ≠ False, и гейт с интерфейсом расходятся здесь намеренно.

    Гейт реагирует ТОЛЬКО на явное False ("разметка была, владельца не опознали") —
    None означает, что диаризации не было вовсе, и красить строку стопом не за что:
    флаг attribution_unclear говорит «атрибуции нельзя верить», а тут атрибуции
    просто нет. Интерфейс же (web/src/pages/Review.tsx, функция attribution)
    ветвится и на None — рисует жёлтое «под вопросом», чтобы None не выглядел
    уверенным «опознан». Расхождение между репозиториями ничем не было покрыто —
    этот тест фиксирует поведение серверной половины, чтобы менять его случайно
    не получилось.
    """
    assert evaluate(_draft(owner_identified=None), FACTS_OK, "zoom") == []


def test_owner_identified_none_with_high_unknown_share_still_raises_attribution():
    """Вторая половина того же условия: доля неопознанных сегментов выше порога
    поднимает флаг сама по себе, независимо от owner_identified."""
    facts = {"diarized": True, "speaker_count": 2, "unknown_share": 0.5}
    assert "attribution_unclear" in evaluate(_draft(owner_identified=None), facts, "zoom")


def test_telegram_origin_raises_foreign():
    assert "foreign_recording" in evaluate(_draft(), FACTS_OK, "telegram-desktop")


def test_unknown_origin_raises_foreign():
    assert "foreign_recording" in evaluate(_draft(), FACTS_OK, "unknown")


def test_downloads_origin_is_not_foreign_by_itself():
    # Скачанные Zoom-записи владельца падают в Downloads — папка не признак чужого.
    assert "foreign_recording" not in evaluate(_draft(), FACTS_OK, "downloads")
