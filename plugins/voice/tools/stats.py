# -*- coding: utf-8 -*-
"""Статистики корпуса — маркеры, по которым видно ИИ-текст.

    python stats.py
    python stats.py --config ../voice.config.yaml

Корпус и файл вывода берутся из конфига: `profile` + `/corpus` и
`profile` + `/stats.json`. Конфиг читается только в main() и в read_corpus()
без аргумента — импортировать модуль можно и без конфига.

Два разных объекта в одном файле, их нельзя путать:

1. Значения ПО СКЛЕЙКЕ всего корпуса (`burstiness`, `one_sentence_para_share`,
   `sentence_len_mean`, `punctuation_per_1000`) — описывают корпус как целое.
   На них ссылается стилевая карта. Порогами они быть не могут: в склейке
   разброс раздут межпостовой вариацией, а линтер меряет ОДИН текст.

2. Пороги (`thresholds`) — считаются по ПО-ПОСТОВЫМ распределениям как
   перцентили. Цель — не выше 5% ложных срабатываний на собственном корпусе.
"""

import argparse
import json
import math
import re
import statistics
from pathlib import Path

from config import add_config_argument, load_or_die

PUNCT_KEYS = ["—", "(", "…", "!", "?", ":", ";"]
SENT_END = re.compile(r"(?<=[.!?…])\s+")

# Минимум предложений, ниже которого статистические проверки не применимы.
# Обоснование см. в _method внутри stats.json и в шапке find_stat_deviations.
MIN_SENTENCES = 5

# Целевая частота ложных срабатываний на собственном корпусе. Перцентиль не
# зашит константой, а подбирается под эту цель: см. pick_min_/pick_max_pct.
MAX_FALSE_POSITIVE_RATE = 0.05


def strip_frontmatter(md: str) -> str:
    if md.startswith("---\n"):
        end = md.find("\n---\n", 3)
        if end != -1:
            return md[end + len("\n---\n"):].strip()
    return md.strip()


def split_sentences(text: str) -> list:
    parts = [p.strip() for p in SENT_END.split(text) if p.strip()]
    return parts


def sentence_lengths(text: str) -> list:
    return [len(s.split()) for s in split_sentences(text)]


def burstiness(lengths: list) -> float:
    """Коэффициент вариации длин предложений. У людей высокий, у ИИ низкий.

    При len(lengths) < 2 возвращает 0.0 — это НЕ «низкий разброс», а
    «величина не определена». Вызывающий код обязан отсеивать такие тексты
    до сравнения с порогом (см. MIN_SENTENCES).
    """
    if len(lengths) < 2:
        return 0.0
    mean = statistics.mean(lengths)
    if mean == 0:
        return 0.0
    return statistics.pstdev(lengths) / mean


def punctuation_per_1000(text: str) -> dict:
    n = max(len(text), 1)
    return {k: text.count(k) * 1000 / n for k in PUNCT_KEYS}


def one_sentence_para_share(text: str) -> float:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return 0.0
    single = sum(1 for p in paras if len(split_sentences(p)) == 1)
    return single / len(paras)


def percentile(values: list, p: float) -> float:
    """Перцентиль с линейной интерполяцией. p в долях единицы."""
    if not values:
        raise ValueError("пустая выборка")
    a = sorted(values)
    if len(a) == 1:
        return float(a[0])
    i = (len(a) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    return float(a[lo] + (a[hi] - a[lo]) * (i - lo))


def pick_min_pct(values: list, max_fp: float = MAX_FALSE_POSITIVE_RATE) -> tuple:
    """Подбирает нижний порог: самый СТРОГИЙ, который даёт не больше max_fp
    ложных срабатываний на этой же выборке.

    Строгость нижнего порога растёт с перцентилем, поэтому берём максимальный
    допустимый. Округление перцентиля до целого процента — чтобы порог не
    садился ровно на конкретное значение выборки.
    """
    best = None
    for i in range(1, 21):  # p01…p20
        p = i / 100
        th = percentile(values, p)
        fp = sum(1 for v in values if v < th)
        if fp / len(values) <= max_fp:
            best = (p, th, fp)
    if best is None:
        raise ValueError("нет перцентиля с допустимой частотой ложных срабатываний")
    return best


def pick_max_pct(values: list, max_fp: float = MAX_FALSE_POSITIVE_RATE) -> tuple:
    """То же для верхнего порога: строгость растёт при УМЕНЬШЕНИИ перцентиля,
    поэтому берём минимальный допустимый."""
    best = None
    for i in range(99, 79, -1):  # p99…p80
        p = i / 100
        th = percentile(values, p)
        fp = sum(1 for v in values if v > th)
        if fp / len(values) <= max_fp:
            best = (p, th, fp)
    if best is None:
        raise ValueError("нет перцентиля с допустимой частотой ложных срабатываний")
    return best


def summarize(values: list) -> dict:
    """Сводка по-постового распределения — обоснование порога."""
    return {
        "n": len(values),
        "min": min(values),
        "p05": percentile(values, 0.05),
        "p10": percentile(values, 0.10),
        "median": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def read_corpus(corpus_dir=None, config=None) -> list:
    """Читает корпус. Без аргументов берёт каталог из конфига."""
    if corpus_dir is None:
        corpus_dir = (config or load_or_die()).corpus_dir
    corpus_dir = Path(corpus_dir)
    return [strip_frontmatter(p.read_text(encoding="utf-8"))
            for p in sorted(corpus_dir.glob("*.md"))]


def compute(texts: list) -> dict:
    """Считает и склеечные величины, и по-постовые пороги."""
    joined = "\n\n".join(texts)
    joined_lengths = sentence_lengths(joined)

    # По-постовые распределения. Пороги калибруются только на тех постах,
    # к которым проверка вообще применима: >= MIN_SENTENCES предложений.
    eligible = [t for t in texts if len(split_sentences(t)) >= MIN_SENTENCES]
    if not eligible:
        raise SystemExit(
            f"В корпусе нет постов с >= {MIN_SENTENCES} предложениями — "
            "пороги не на чем калибровать.")

    per_post_burst = [burstiness(sentence_lengths(t)) for t in eligible]
    per_post_share = [one_sentence_para_share(t) for t in eligible]

    # Перцентиль подбирается под цель по частоте ложных срабатываний,
    # а не назначается заранее.
    b_pct, b_min, fp_b = pick_min_pct(per_post_burst)
    s_pct, s_max, fp_s = pick_max_pct(per_post_share)

    return {
        "posts": len(texts),
        "sentence_len_mean": statistics.mean(joined_lengths),
        "burstiness": burstiness(joined_lengths),
        "punctuation_per_1000": punctuation_per_1000(joined),
        "one_sentence_para_share": one_sentence_para_share(joined),
        "_joined_note": (
            "Четыре величины выше посчитаны по СКЛЕЙКЕ всего корпуса. Они "
            "описывают корпус, на них ссылается style-card.md. Порогами для "
            "одиночного текста они быть не могут."),
        "per_post": {
            "min_sentences": MIN_SENTENCES,
            "eligible_posts": len(eligible),
            "skipped_posts": len(texts) - len(eligible),
            "burstiness": summarize(per_post_burst),
            "one_sentence_para_share": summarize(per_post_share),
        },
        "thresholds": {
            "min_sentences": MIN_SENTENCES,
            "burstiness_min": b_min,
            "one_sentence_para_share_max": s_max,
            "_method": (
                f"Пороги — перцентили ПО-ПОСТОВЫХ распределений, посчитанных "
                f"на постах с >= {MIN_SENTENCES} предложениями "
                f"({len(eligible)} из {len(texts)}). "
                f"burstiness_min = p{int(round(b_pct*100)):02d}, "
                f"one_sentence_para_share_max = p{int(round(s_pct*100))}. "
                f"Перцентиль не назначен заранее, а подобран прямо под цель "
                f"«не выше {int(MAX_FALSE_POSITIVE_RATE*100)}% ложных "
                f"срабатываний на собственном корпусе»: перебор p01…p20 (снизу) "
                f"и p99…p80 (сверху), берётся самый строгий порог, у которого "
                f"измеренная частота ещё укладывается в цель. Прежний метод "
                f"(порог как доля от значения по СКЛЕЙКЕ) давал 57% и 20% "
                f"ложных срабатываний — склейка раздувает разброс межпостовой "
                f"вариацией, а линтер меряет один текст."),
            "_min_sentences_rationale": (
                f"burstiness — коэффициент вариации; его относительная "
                f"погрешность ~1/sqrt(2(n-1)): 71% при n=2, 50% при n=3, "
                f"41% при n=4, 35% при n=5. Ниже пяти предложений шум самой "
                f"метрики больше того разброса, который она должна ловить: "
                f"в корпусе при n=2 значения лежат в диапазоне 0.05–0.95, "
                f"при n=5 — уже 0.18–0.66. Плюс burstiness() возвращает 0.0 "
                f"при n<2, то есть без отсечки любой короткий текст "
                f"гарантированно валит проверку. Тот же минимум чинит и долю "
                f"односложных абзацев: у поста в один абзац она равна 1.0 по "
                f"определению жанра, а не по вине текста."),
            "_percentile": {
                "burstiness_min": round(b_pct, 2),
                "one_sentence_para_share_max": round(s_pct, 2),
                "target_max_false_positive_rate": MAX_FALSE_POSITIVE_RATE,
            },
            "_false_positive_rate_on_corpus": {
                "_denominator": (
                    f"{len(eligible)} постов, к которым проверка применима "
                    f"(из {len(texts)}); остальные {len(texts) - len(eligible)} "
                    f"короче {MIN_SENTENCES} предложений — по ним вердикт "
                    f"не выносится"),
                "burstiness": f"{fp_b}/{len(eligible)} = "
                              f"{100 * fp_b / len(eligible):.1f}%",
                "one_sentence_para_share": f"{fp_s}/{len(eligible)} = "
                                           f"{100 * fp_s / len(eligible):.1f}%",
            },
        },
        "_note": "Пороги — отсев, а не цель оптимизации. Риск Гудхарта: если подгонять тексты под пороги, метрика перестанет мерить.",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_argument(ap)
    args = ap.parse_args()
    cfg = load_or_die(args.config)

    texts = read_corpus(cfg.corpus_dir)
    if not texts:
        raise SystemExit(f"Пустой корпус: {cfg.corpus_dir}")

    data = compute(texts)

    out = cfg.stats_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
