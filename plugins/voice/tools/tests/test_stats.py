import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats import (
    burstiness,
    one_sentence_para_share,
    punctuation_per_1000,
    sentence_lengths,
    split_sentences,
    strip_frontmatter,
)


def test_strip_frontmatter():
    md = "---\ndate: 2025-11-14\n---\n\nТекст поста."
    assert strip_frontmatter(md) == "Текст поста."


def test_strip_frontmatter_noop_without_it():
    assert strip_frontmatter("Просто текст.") == "Просто текст."


def test_split_sentences():
    assert split_sentences("Раз. Два! Три?") == ["Раз.", "Два!", "Три?"]


def test_sentence_lengths_counts_words():
    assert sentence_lengths("Раз два три. Четыре.") == [3, 1]


def test_burstiness_zero_for_uniform():
    assert burstiness([5, 5, 5, 5]) == 0.0


def test_burstiness_positive_for_varied():
    assert burstiness([1, 10, 3, 20]) > 0.5


def test_punctuation_per_1000_counts_em_dash():
    text = "а — б" * 100
    assert punctuation_per_1000(text)["—"] > 100


def test_one_sentence_para_share():
    text = "Одно предложение.\n\nДва предложения. Ещё одно.\n\nОпять одно."
    assert one_sentence_para_share(text) == 2 / 3


# --- пороги: перцентили по-постовых распределений ---

from stats import (  # noqa: E402
    MAX_FALSE_POSITIVE_RATE,
    MIN_SENTENCES,
    compute,
    percentile,
    pick_max_pct,
    pick_min_pct,
)


def test_percentile_interpolates():
    assert percentile([0, 10], 0.5) == 5.0
    assert percentile([1, 2, 3, 4, 5], 0.0) == 1.0
    assert percentile([1, 2, 3, 4, 5], 1.0) == 5.0


def test_percentile_single_value():
    assert percentile([7.0], 0.5) == 7.0


def test_pick_min_pct_respects_false_positive_target():
    values = [float(i) for i in range(100)]
    p, th, fp = pick_min_pct(values, max_fp=0.05)
    assert fp / len(values) <= 0.05
    assert sum(1 for v in values if v < th) == fp


def test_pick_max_pct_respects_false_positive_target():
    values = [float(i) for i in range(100)]
    p, th, fp = pick_max_pct(values, max_fp=0.05)
    assert fp / len(values) <= 0.05
    assert sum(1 for v in values if v > th) == fp


def _post(n_sent: int, words: int = 5) -> str:
    return " ".join([" ".join(["сл"] * words) + "."] * n_sent)


def test_compute_thresholds_come_from_per_post_not_from_joined():
    """Порог не должен зависеть от значения по склейке.

    Каждый пост внутри себя ровный (burstiness=0), но посты разной длины —
    значит по склейке разброс большой. Старая формула брала бы порог от
    склейки; новая обязана взять его из по-постового распределения.
    """
    texts = [_post(MIN_SENTENCES, w) for w in (3, 12, 30, 5, 20, 8)]
    data = compute(texts)
    assert data["burstiness"] > 0.5           # склейка: разброс большой
    assert data["thresholds"]["burstiness_min"] == 0.0  # по постам: нулевой
    assert data["per_post"]["burstiness"]["median"] == 0.0


def test_compute_keeps_joined_corpus_values():
    texts = [_post(MIN_SENTENCES) for _ in range(10)]
    data = compute(texts)
    for key in ("sentence_len_mean", "burstiness",
                "punctuation_per_1000", "one_sentence_para_share"):
        assert key in data


def test_compute_calibrates_only_on_eligible_posts():
    texts = [_post(MIN_SENTENCES) for _ in range(10)] + [_post(1) for _ in range(7)]
    data = compute(texts)
    assert data["posts"] == 17
    assert data["per_post"]["eligible_posts"] == 10
    assert data["per_post"]["skipped_posts"] == 7


def test_compute_records_min_sentences_in_thresholds():
    data = compute([_post(MIN_SENTENCES) for _ in range(10)])
    assert data["thresholds"]["min_sentences"] == MIN_SENTENCES


def _synthetic_corpus() -> list:
    """Корпус-заменитель: посты разной длины и разного ритма.

    Нужен там, где раньше стоял живой корпус владельца голоса. Пакет
    отчуждаемый, чужих текстов в нём нет; обещание «не выше 5% ложных
    срабатываний» — свойство САМОГО СПОСОБА подбора порога (pick_min_pct /
    pick_max_pct), а не конкретных текстов, поэтому проверяется на любом
    достаточно разнообразном корпусе.
    """
    import random
    rnd = random.Random(20260903)
    texts = []
    for i in range(60):
        n_sent = rnd.randint(5, 14)
        paras, sent = [], []
        for _ in range(n_sent):
            sent.append(" ".join(["сл"] * rnd.randint(2, 22)) + ".")
            if rnd.random() < 0.4:
                paras.append(" ".join(sent))
                sent = []
        if sent:
            paras.append(" ".join(sent))
        texts.append("\n\n".join(paras))
    return texts


def test_real_corpus_thresholds_hit_false_positive_target(profile_config):
    """Прогон по корпусу: обещанные <=5% должны выполняться.

    Если рядом сконфигурирован настоящий профиль с непустым корпусом —
    считаем по нему (это и есть проверка «пороги в проде не поехали»).
    Иначе — по синтетическому корпусу-заменителю.
    """
    from stats import read_corpus, sentence_lengths as sl, split_sentences as ss

    if profile_config is not None and profile_config.corpus_dir.exists():
        texts = read_corpus(profile_config.corpus_dir)
    else:
        texts = []
    if not texts:
        texts = _synthetic_corpus()
    data = compute(texts)
    th = data["thresholds"]
    eligible = [t for t in texts if len(ss(t)) >= th["min_sentences"]]
    fp_b = sum(1 for t in eligible
               if burstiness(sl(t)) < th["burstiness_min"])
    fp_s = sum(1 for t in eligible
               if one_sentence_para_share(t) > th["one_sentence_para_share_max"])
    assert fp_b / len(eligible) <= MAX_FALSE_POSITIVE_RATE
    assert fp_s / len(eligible) <= MAX_FALSE_POSITIVE_RATE
