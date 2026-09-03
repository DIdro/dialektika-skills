# -*- coding: utf-8 -*-
"""Регрессия на живых фикстурах: tools/fixtures/.

Прогон линтера на текстах с заранее известными метриками. Ожидания
зафиксированы по фактическому прогону; пояснения — в fixtures/README.md.

Пороги берутся из fixtures/stats.reference.json (фикстура `stats` в
conftest.py) — эталон ПОД ФИКСТУРЫ, а не чей-то профиль: пакет отчуждаемый,
и тесты обязаны проходить на голом клоне без корпуса и без stats.json.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lint import is_finding, lint
from stats import (
    burstiness,
    one_sentence_para_share,
    sentence_lengths,
    split_sentences,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def kinds(text: str, stats: dict) -> list:
    return [f["kind"] for f in lint(text, stats) if is_finding(f)]


# --- фикстуры на месте и не подменены ---

def test_fixtures_exist():
    for name in ("bad.txt", "hole.txt", "checklist.txt", "article-like.txt",
                 "stats.reference.json", "README.md"):
        assert (FIXTURES / name).exists(), f"нет фикстуры {name}"


def test_bad_txt_metrics_match_task6_report():
    """Сверка с дословными числами из README фикстур — файл тот же."""
    t = read("bad.txt")
    assert sentence_lengths(t) == [7, 3, 6, 3, 4, 3, 5]
    assert round(burstiness(sentence_lengths(t)), 3) == 0.338
    assert round(one_sentence_para_share(t), 3) == 0.800


def test_hole_txt_metrics_match_task6_report():
    t = read("hole.txt")
    assert sentence_lengths(t) == [28, 7, 8, 16, 8, 7]
    assert round(burstiness(sentence_lengths(t)), 3) == 0.622
    assert round(one_sentence_para_share(t), 3) == 0.800


# --- ожидаемый вывод линтера ---

def test_bad_txt_is_caught(stats):
    """Линтер обязан не молчать на тексте, который автор забраковал бы."""
    found = kinds(read("bad.txt"), stats)
    assert "hook" in found
    assert "short_run" in found


def test_bad_txt_is_long_enough_for_stat_checks(stats):
    """7 предложений — отсечка не должна глушить статистику на этом тексте."""
    items = lint(read("bad.txt"), stats)
    assert not any(f["kind"] == "stat_not_applicable" for f in items)


def test_bad_txt_no_longer_flagged_by_burstiness(stats):
    """0,338 проходит эталонный порог 0,20 — и это верно.

    Разброс длин предложений — грубая метрика: порог, который ловил бы
    bad.txt по ней, помечал бы и половину нормального корпуса. Текст
    ловится крючком (hook) и серией коротких предложений (short_run),
    а не статистикой.
    """
    assert "burstiness" not in kinds(read("bad.txt"), stats)


def test_hole_txt_stays_a_known_hole(stats):
    """Известная дыра — механическая часть её не ловит и не притворяется.

    Ловит её базовый бинарный вопрос про разбивку на односложные абзацы,
    который задаётся независимо от вывода линтера. Если тест упал — линтер
    начал ловить hole.txt: проверить цену по ложным срабатываниям на корпусе
    и осознанно пересмотреть фикстуру, а не править ожидание.
    """
    assert kinds(read("hole.txt"), stats) == []


def test_hole_txt_gets_a_verdict_not_a_skip(stats):
    """«Чисто» на hole.txt должно быть вердиктом, а не отсечкой по длине."""
    t = read("hole.txt")
    assert len(split_sentences(t)) >= stats["thresholds"]["min_sentences"]
    assert not any(f["kind"] == "stat_not_applicable" for f in lint(t, stats))


# --- checklist.txt: страховка от вырождения по доле списочных символов ---

def test_checklist_txt_is_mostly_list_by_chars():
    """Доля списочных СИМВОЛОВ (не строк) — сверка с числами из README."""
    from lint import prose_only
    t = read("checklist.txt")
    lines_all = [l.strip() for l in t.split("\n") if l.strip()]
    lines_prose = [l.strip() for l in prose_only(t).split("\n") if l.strip()]
    chars_all = sum(len(l) for l in lines_all)
    chars_prose = sum(len(l) for l in lines_prose)
    assert chars_all == 415
    assert chars_all - chars_prose == 335
    assert round((chars_all - chars_prose) / chars_all, 2) == 0.81


def test_checklist_txt_gets_no_stat_verdict(stats):
    """81% списочных символов — заметно выше MAX_LIST_SHARE (0,40)."""
    from lint import find_stat_deviations
    found = find_stat_deviations(read("checklist.txt"), stats)
    assert [f["kind"] for f in found] == ["stat_not_applicable"]
    assert "список" in found[0]["match"] or "чек-лист" in found[0]["match"]


def test_checklist_txt_overall_verdict_is_clean(stats):
    """stat_not_applicable — не находка, других находок в тексте нет."""
    t = read("checklist.txt")
    assert kinds(t, stats) == []
