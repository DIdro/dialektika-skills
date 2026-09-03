# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lint import (
    find_hooks,
    find_short_runs,
    find_stat_deviations,
    is_finding,
    lint,
    prose_only,
)

STATS = {"thresholds": {"min_sentences": 5,
                        "burstiness_min": 0.5,
                        "one_sentence_para_share_max": 0.4}}


def test_finds_hook_phrase():
    found = find_hooks("Мы поехали. И знаете что? Всё сломалось.")
    assert len(found) == 1
    assert found[0]["kind"] == "hook"


def test_finds_several_hooks():
    found = find_hooks("Сюжетный поворот. А потом оказалось, что нет.")
    assert len(found) == 2


def test_no_hooks_in_clean_text():
    assert find_hooks("Обычный текст без крючков внимания.") == []


def test_finds_triad_of_short_sentences():
    found = find_short_runs("Есть машина. Есть два ключа. Ни один не подходит.")
    assert len(found) == 1
    assert found[0]["kind"] == "short_run"


def test_long_sentences_are_not_a_run():
    text = ("Мы довольно долго стояли возле сервиса и обсуждали, что делать дальше. "
            "Потом решили поехать домой на такси, потому что вариантов не осталось.")
    assert find_short_runs(text) == []


def test_flags_low_burstiness():
    text = " ".join(["Слово слово слово слово слово."] * 8)
    found = find_stat_deviations(text, STATS)
    assert any(f["kind"] == "burstiness" for f in found)


def test_flags_too_many_one_sentence_paragraphs():
    # Не меньше min_sentences (5), иначе проверка вообще не выносит вердикт.
    text = "Один.\n\nДва.\n\nТри.\n\nЧетыре.\n\nПять.\n\nШесть."
    found = find_stat_deviations(text, STATS)
    assert any(f["kind"] == "one_sentence_paras" for f in found)


def test_lint_merges_all_findings():
    text = "Есть А. Есть Б. Ни одно не В. И знаете что? Всё."
    assert len([f for f in lint(text, STATS) if is_finding(f)]) >= 2


# --- защита от коротких текстов ---

def test_short_text_gets_no_stat_verdict():
    """Меньше min_sentences — проверка не выносит вердикт вообще.

    Три ровных предложения: burstiness = 0.0, доля односложных абзацев = 1.0.
    Обе величины формально «нарушают» порог, но текст просто слишком короткий.
    """
    text = "Раз два три.\n\nЧетыре пять шесть.\n\nСемь восемь девять."
    found = find_stat_deviations(text, STATS)
    kinds = {f["kind"] for f in found}
    assert kinds == {"stat_not_applicable"}
    assert "burstiness" not in kinds
    assert "one_sentence_paras" not in kinds


def test_one_sentence_text_gets_no_stat_verdict():
    """burstiness() возвращает 0.0 при <2 предложениях — это не нарушение."""
    found = find_stat_deviations("Одно предложение и всё.", STATS)
    assert [f["kind"] for f in found] == ["stat_not_applicable"]


def test_stat_not_applicable_is_not_a_finding():
    text = "Раз два три. Четыре пять шесть."
    items = lint(text, STATS)
    assert [f for f in items if is_finding(f)] == []
    assert any(f["kind"] == "stat_not_applicable" for f in items)


def test_stat_not_applicable_message_names_the_reason():
    found = find_stat_deviations("Короткий текст.", STATS)
    assert "слишком" in found[0]["match"]
    assert "5" in found[0]["match"]


def test_long_enough_text_is_still_checked():
    """Отсечка не должна глушить проверку на текстах нормальной длины."""
    text = " ".join(["Слово слово слово слово слово."] * 8)
    found = find_stat_deviations(text, STATS)
    assert any(f["kind"] == "burstiness" for f in found)
    assert not any(f["kind"] == "stat_not_applicable" for f in found)


def test_min_sentences_falls_back_to_module_default():
    """Порогов может не быть в stats.json — минимум всё равно действует."""
    from lint import MIN_SENTENCES
    found = find_stat_deviations("Одно.", {"thresholds": {}})
    assert found[0]["kind"] == "stat_not_applicable"
    assert str(MIN_SENTENCES) in found[0]["match"]


# --- prose_only ---

def test_prose_only_drops_dash_bullets():
    text = "Вступление.\n- Пункт раз.\n- Пункт два.\nЗаключение."
    assert prose_only(text) == "Вступление.\nЗаключение."


def test_prose_only_drops_various_bullet_markers():
    text = "\n".join([
        "Проза.",
        "• буллет точка",
        "– буллет тире короткое",
        "— буллет тире длинное",
        "* буллет звёздочка",
        "Ещё проза.",
    ])
    assert prose_only(text) == "Проза.\nЕщё проза."


def test_prose_only_drops_emoji_bullets():
    text = "Проза.\n✔️ Пункт с галочкой.\n🔸 Пункт с кружком.\nЕщё проза."
    assert prose_only(text) == "Проза.\nЕщё проза."


def test_prose_only_drops_numbered_list_items():
    text = "\n".join([
        "Проза.",
        "1. Первый пункт.",
        "2) Второй пункт.",
        "3 . Третий пункт с пробелом перед точкой.",
        "Ещё проза.",
    ])
    assert prose_only(text) == "Проза.\nЕщё проза."


def test_prose_only_drops_quotes():
    text = "Проза.\n> Цитата первая.\n> Цитата вторая.\nЕщё проза."
    assert prose_only(text) == "Проза.\nЕщё проза."


def test_prose_only_drops_headings():
    text = "# Заголовок первого уровня\nПроза после заголовка.\n## Подзаголовок\nЕщё проза."
    assert prose_only(text) == "Проза после заголовка.\nЕщё проза."


def test_prose_only_mixed_text_keeps_only_prose():
    text = "\n".join([
        "# Заголовок",
        "Первый абзац прозы, длинное предложение о разном.",
        "- буллет один",
        "- буллет два",
        "> цитата",
        "1. пункт нумерованный",
        "✔️ пункт с эмодзи",
        "Второй абзац прозы, тоже длинное предложение.",
    ])
    assert prose_only(text) == (
        "Первый абзац прозы, длинное предложение о разном.\n"
        "Второй абзац прозы, тоже длинное предложение."
    )


def test_prose_only_leaves_clean_prose_untouched():
    text = ("Мы довольно долго стояли возле сервиса и обсуждали, что делать дальше.\n\n"
            "Потом решили поехать домой на такси, потому что вариантов не осталось.")
    assert prose_only(text) == text


def test_prose_only_keeps_blank_lines_as_paragraph_separators():
    text = "Абзац один.\n\n- буллет\n\nАбзац два."
    assert prose_only(text) == "Абзац один.\n\n\nАбзац два."


# --- страховка от вырождения на списочных текстах ---

def test_checklist_text_gets_no_stat_verdict():
    """Текст, где большинство строк — буллеты, не годится для замера ритма
    прозы: после выброса списков от него почти ничего не остаётся."""
    text = "\n".join([
        "Коротко.",
        "- пункт раз, довольно длинный, чтобы не упасть по короткому прогону",
        "- пункт два, тоже длинный пункт списка для ровного счёта",
        "- пункт три, снова длинный пункт списка для устойчивости счёта",
        "- пункт четыре, длинный пункт списка чтобы список был больше прозы",
        "- пункт пять, длинный пункт списка для верности расчёта доли",
    ])
    found = find_stat_deviations(text, STATS)
    assert [f["kind"] for f in found] == ["stat_not_applicable"]
    assert "список" in found[0]["match"] or "чек-лист" in found[0]["match"]


def test_checklist_flag_is_not_a_finding():
    text = "\n".join([
        "Коротко.",
        "- пункт раз, длинный пункт списка чтобы не упасть по короткому прогону",
        "- пункт два, длинный пункт списка чтобы список был больше прозы",
        "- пункт три, длинный пункт списка для устойчивости счёта",
        "- пункт четыре, длинный пункт списка для верности расчёта доли",
    ])
    items = lint(text, STATS)
    assert [f for f in items if is_finding(f)] == []
    assert any(f["kind"] == "stat_not_applicable" for f in items)


def test_prose_heavy_text_with_a_few_bullets_still_gets_a_verdict(stats=STATS):
    """Немного списка (в пределах MAX_LIST_SHARE) не должно глушить проверку
    на тексте, где прозы всё ещё большинство."""
    prose_sent = "Слово слово слово слово слово."
    text = "\n\n".join([prose_sent] * 8) + "\n\n- один короткий пункт списка"
    found = find_stat_deviations(text, stats)
    assert not any(f["kind"] == "stat_not_applicable" for f in found)
    assert any(f["kind"] == "burstiness" for f in found)


# --- MAX_LIST_SHARE считается по символам, не по строкам ---

def _text_with_list_char_share(share: float) -> str:
    """Собирает текст с заданной (примерной) долей списочных СИМВОЛОВ.

    Проза — 6 разных по длине предложений (нужно для валидного применимого
    прогона), список — один буллет нужной длины, чтобы выйти на долю `share`
    от общего числа символов.
    """
    prose_lines = [
        "Мы довольно долго обсуждали, что делать дальше в этой ситуации.",
        "Потом решили ехать домой на такси.",
        "Вариантов совсем не осталось, все разошлись расстроенные.",
        "Утром стало немного легче, но вопрос так и завис.",
        "К обеду нашли временное решение и вернулись к работе.",
        "Вечером собрались снова, чтобы подвести итоги дня.",
    ]
    prose_chars = sum(len(l) for l in prose_lines)
    # share = list_chars / (prose_chars + list_chars) => list_chars = ...
    list_chars = int(share * prose_chars / (1 - share))
    bullet = "- " + ("х" * max(list_chars - 2, 0))
    return "\n\n".join(prose_lines) + "\n\n" + bullet


def test_max_list_share_is_below_half():
    """Порог должен оставаться строго ниже 0.5 — иначе он совпадёт с
    отдельным правилом «символов пропало больше половины» (см. комментарий
    у MAX_LIST_SHARE) и оба предохранителя схлопнутся в один."""
    from lint import MAX_LIST_SHARE
    assert MAX_LIST_SHARE == 0.40
    assert MAX_LIST_SHARE < 0.5


def test_list_share_just_below_threshold_still_gets_a_verdict():
    text = _text_with_list_char_share(0.35)
    found = find_stat_deviations(text, STATS)
    assert not any(f["kind"] == "stat_not_applicable" for f in found)


def test_list_share_just_above_threshold_gets_no_verdict():
    text = _text_with_list_char_share(0.45)
    found = find_stat_deviations(text, STATS)
    assert [f["kind"] for f in found] == ["stat_not_applicable"]


def test_real_articles_are_all_applicable(profile_config):
    """Регрессия: с долей по СТРОКАМ и порогом 30% нормальные длинные статьи
    проваливались в stat_not_applicable — хотя ни одна из них не чек-лист.
    Доля по СИМВОЛАМ и порог 0.40 обязаны пропускать их с запасом.

    Проверяется на фикстуре `article-like.txt` (33% списочных символов —
    примерно там же, где живут реальные статьи), а если рядом сконфигурирован
    настоящий профиль с `articles/`, то и на всех его статьях. На голом клоне
    пакета профиля нет — это норма, фикстуры достаточно.
    """
    from pathlib import Path as _Path
    from stats import strip_frontmatter

    permissive = {"thresholds": {"min_sentences": 5,
                                 "burstiness_min": 0.0,
                                 "one_sentence_para_share_max": 1.0}}

    def check(name: str, text: str):
        found = find_stat_deviations(strip_frontmatter(text), permissive)
        assert not any(item["kind"] == "stat_not_applicable" for item in found), (
            f"{name} не должна попадать в stat_not_applicable")

    fixture = _Path(__file__).resolve().parents[1] / "fixtures" / "article-like.txt"
    check(fixture.name, fixture.read_text(encoding="utf-8"))

    if profile_config is None or not profile_config.articles_dir.exists():
        return
    for f in sorted(profile_config.articles_dir.glob("*.md")):
        check(f.name, f.read_text(encoding="utf-8"))
