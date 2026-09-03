# -*- coding: utf-8 -*-
"""Механическая часть списка запретов.

    python lint.py путь/к/тексту.md
    python lint.py путь/к/тексту.md --config ../voice.config.yaml
    python lint.py путь/к/тексту.md --stats путь/к/stats.json

Пороги читаются из `profile/stats.json` (путь берётся из конфига). Пороги,
формулы и списки в этом файле — откалиброванные величины: править их
означает пересчитывать ложные срабатывания на корпусе.
"""

import argparse
import json
import re
from pathlib import Path

from config import add_config_argument, load_or_die
from stats import (
    MIN_SENTENCES,
    burstiness,
    one_sentence_para_share,
    sentence_lengths,
    split_sentences,
)

HOOKS = [
    "и знаете что",
    "сюжетный поворот",
    "а потом оказалось",
    "спойлер",
    "и тут началось самое интересное",
    "самое интересное впереди",
    "но обо всём по порядку",
    "что было дальше",
]

SHORT_RUN_MIN = 3
SHORT_SENTENCE_WORDS = 6

# Доля СИМВОЛОВ (не строк!) в непрозаических строках, выше которой текст —
# чек-лист, а не проза, и статистические проверки ритма по нему не имеют
# смысла (обоснование — в fixtures/README.md).
#
# Считать по строкам, а не по символам, было ошибкой: буллет — короткая
# строка, абзац прозы — длинный, и счёт по строкам переоценивает долю списка
# примерно вдвое. На трёх реальных длинных статьях (только тело статьи, без
# меню и подвала площадки) счёт по символам даёт 31.8% / 28.1% / 24.7% —
# максимум 31.8%. Порог 40% даёт каждой статье не
# меньше 8 п.п. (≈26% относительно максимума) запаса, и остаётся заметно
# ниже зоны, где список — уже большинство текста (у явно списочных постов
# корпуса — от 48% и выше), то есть настоящий чек-лист по-прежнему ловится
# с запасом. Порог держим строго ниже 50%, чтобы не совпасть с отдельным
# правилом «символов пропало больше половины» ниже — иначе оба
# предохранителя схлопнутся в одну и ту же проверку.
MAX_LIST_SHARE = 0.40

# Маркер списка: •, -, –, —, *, а также нумерация «1.» / «1)» / «1 .».
_BULLET_RE = re.compile(r"^[•*\-–—]")
_NUMBERED_RE = re.compile(r"^\d+\s*[.)]")
# Markdown-заголовок: #, ## и далее.
_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")
# Цитата.
_QUOTE_RE = re.compile(r"^>")
# Эмодзи-буллеты (✔️, 🔸, ➡️, 👇, ✨ и подобные) — символы из блоков
# "Misc symbols & dingbats" (U+2600–U+27BF) и основных emoji-плоскостей
# (U+1F300–U+1FAFF), которыми в постах часто начинают списочные строки.
_EMOJI_BULLET_RE = re.compile(
    "^[☀-➿\U0001F300-\U0001FAFF]"
)

_NON_PROSE_LINE_RES = (_HEADING_RE, _QUOTE_RE, _BULLET_RE, _NUMBERED_RE,
                        _EMOJI_BULLET_RE)


def _is_prose_line(line: str) -> bool:
    s = line.lstrip()
    if not s:
        return True  # пустые строки — разделители абзацев, не списки
    return not any(r.match(s) for r in _NON_PROSE_LINE_RES)


def prose_only(text: str) -> str:
    """Выбрасывает списки, цитаты и заголовки — оставляет только прозу.

    Нужна потому, что `burstiness` и `one_sentence_para_share` считаются по
    предложениям/абзацам как есть: несломанный сплиттер склеивает буллет-лист
    в одно длинное «предложение», а сломанный на буллетах — режет его на
    десяток двух-четырёхсловных. Оба варианта — артефакт списка, а не сигнал
    о ритме прозы. См. fixtures/README.md.

    Строка считается списочной/нерозаической, если ПОСЛЕ обрезки пробелов
    слева начинается с маркера списка, нумерации, цитаты или markdown-
    заголовка. Пустые строки (разделители абзацев) сохраняются как есть.
    """
    return "\n".join(line for line in text.split("\n") if _is_prose_line(line))


def find_hooks(text: str) -> list:
    low = text.lower()
    out = []
    for h in HOOKS:
        for m in re.finditer(re.escape(h), low):
            out.append({"kind": "hook", "match": text[m.start():m.end()]})
    return out


def find_short_runs(text: str) -> list:
    sents = split_sentences(text)
    out, run = [], []
    for s in sents:
        if len(s.split()) <= SHORT_SENTENCE_WORDS:
            run.append(s)
        else:
            if len(run) >= SHORT_RUN_MIN:
                out.append({"kind": "short_run", "match": " ".join(run)})
            run = []
    if len(run) >= SHORT_RUN_MIN:
        out.append({"kind": "short_run", "match": " ".join(run)})
    return out


def find_stat_deviations(text: str, stats: dict) -> list:
    """Статистические проверки. На коротком и на списочном тексте вердикта
    НЕ выносит.

    burstiness — коэффициент вариации длин предложений; при менее чем двух
    предложениях он не определён и возвращается как 0.0, то есть без отсечки
    любой короткий текст гарантированно «нарушает» порог. Доля односложных
    абзацев на посте в один абзац равна 1.0 по определению жанра. Поэтому
    ниже min_sentences обе проверки возвращают не «чисто» и не «нарушение»,
    а отдельный вид `stat_not_applicable` — «текст слишком короткий для этой
    проверки». Он НЕ является находкой и не отменяет вердикт «чисто».

    Обе метрики считаются по `prose_only(text)`, а не по тексту как есть:
    буллет-лист, склеенный сплиттером в одно предложение на сто слов, или
    разрезанный на серию из двух-четырёх слов, раздувает или занижает
    burstiness как артефакт вёрстки, а не ритма прозы (см. fixtures/README.md).
    Доля меряется по СИМВОЛАМ, а не по числу строк: буллет — короткая строка,
    абзац прозы — длинный, и счёт по строкам переоценивает долю списка
    примерно вдвое (см. MAX_LIST_SHARE). Если после выброса списков осталось
    меньше половины исходных символов, или списочные символы — больше
    MAX_LIST_SHARE от текста, прозы почти не осталось: это чек-лист, а не
    статья, и мерить его ритм бессмысленно — вердикт тоже не выносится.
    """
    th = stats.get("thresholds", {})
    min_sent = th.get("min_sentences", MIN_SENTENCES)

    prose = prose_only(text)
    lines_all = [l.strip() for l in text.split("\n") if l.strip()]
    lines_prose = [l.strip() for l in prose.split("\n") if l.strip()]
    chars_all = sum(len(l) for l in lines_all)
    chars_prose = sum(len(l) for l in lines_prose)
    if chars_all:
        list_share = (chars_all - chars_prose) / chars_all
        if chars_prose < chars_all / 2 or list_share > MAX_LIST_SHARE:
            return [{"kind": "stat_not_applicable",
                     "match": (f"списочных символов {list_share:.0%} "
                               f"({chars_all - chars_prose} из "
                               f"{chars_all}) — текст похож на список/"
                               f"чек-лист, а не на прозу; статистические "
                               f"проверки ритма (разброс длин, доля "
                               f"односложных абзацев) по нему не выносят "
                               f"вердикт")}]

    n_sent = len(split_sentences(prose))
    if n_sent < min_sent:
        return [{"kind": "stat_not_applicable",
                 "match": (f"предложений {n_sent} < {min_sent} — текст слишком "
                           f"короткий для статистических проверок "
                           f"(разброс длин, доля односложных абзацев); "
                           f"вердикт по ним не выносится")}]

    out = []
    b = burstiness(sentence_lengths(prose))
    b_min = th.get("burstiness_min")
    if b_min is not None and b < b_min:
        out.append({"kind": "burstiness",
                    "match": f"разброс длин предложений {b:.2f} < порога {b_min:.2f}"})
    share = one_sentence_para_share(prose)
    s_max = th.get("one_sentence_para_share_max")
    if s_max is not None and share > s_max:
        out.append({"kind": "one_sentence_paras",
                    "match": f"доля односложных абзацев {share:.2f} > порога {s_max:.2f}"})
    return out


# Виды, которые не являются находками: это отметки «проверка не применялась».
NON_FINDING_KINDS = {"stat_not_applicable"}


def is_finding(item: dict) -> bool:
    return item.get("kind") not in NON_FINDING_KINDS


def lint(text: str, stats: dict) -> list:
    return find_hooks(text) + find_short_runs(text) + find_stat_deviations(text, stats)


def main():
    ap = argparse.ArgumentParser(description="Механическая часть списка запретов")
    ap.add_argument("path", help="путь к проверяемому тексту")
    ap.add_argument("--stats", default=None,
                    help="путь к stats.json (по умолчанию — profile/stats.json из конфига)")
    add_config_argument(ap)
    args = ap.parse_args()

    stats_path = Path(args.stats) if args.stats else load_or_die(args.config).stats_path
    if not stats_path.exists():
        raise SystemExit(
            f"нет файла порогов {stats_path} — собери его: python stats.py "
            f"(нужен непустой profile/corpus)")

    text = Path(args.path).read_text(encoding="utf-8")
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    items = lint(text, stats)
    findings = [f for f in items if is_finding(f)]
    notes = [f for f in items if not is_finding(f)]
    for f in findings:
        print(f"[{f['kind']}] {f['match']}")
    for f in notes:
        print(f"[~{f['kind']}] {f['match']}")
    if not findings:
        print("чисто")


if __name__ == "__main__":
    main()
