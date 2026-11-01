from app.telemost.title import (
    caption_for,
    clean_title,
    file_header,
    slugify,
)


# ── clean_title: normalize what the model returns into a single bare line ──

def test_clean_title_strips_wrapping_quotes():
    assert clean_title('"Мир Здоровья — планёрка"') == "Мир Здоровья — планёрка"
    assert clean_title("«Звонок по CRM»") == "Звонок по CRM"


def test_clean_title_drops_trailing_period():
    assert clean_title("Обсуждение бюджета.") == "Обсуждение бюджета"


def test_clean_title_takes_first_line_only():
    assert clean_title("Заголовок звонка\nещё что-то") == "Заголовок звонка"


def test_clean_title_strips_label_prefix():
    assert clean_title("Заголовок: Планёрка с МЗ") == "Планёрка с МЗ"


def test_clean_title_empty_returns_none():
    assert clean_title("") is None
    assert clean_title("   ") is None
    assert clean_title(None) is None


# ── caption_for: what shows under the transcript document in Telegram ──

def test_caption_without_title_is_the_legacy_line():
    assert caption_for(None, 5) == "📝 Расшифровка со спикерами (5 чел.)"


def test_caption_with_title_prepends_it():
    assert caption_for("МЗ — планёрка по CRM", 3) == (
        "📝 МЗ — планёрка по CRM\nРасшифровка со спикерами (3 чел.)"
    )


# ── file_header: title line prepended inside the .txt body ──

def test_file_header_empty_when_no_title():
    assert file_header(None) == ""


def test_file_header_with_title():
    assert file_header("Звонок") == "Звонок\n\n"


# ── slugify: filesystem-safe, identifiable transcript filename ──

def test_slugify_keeps_cyrillic_and_replaces_separators():
    assert slugify("Мир Здоровья — планёрка", fallback="x") == "Мир_Здоровья_планёрка"


def test_slugify_truncates_long_titles():
    out = slugify("слово " * 30, fallback="x", maxlen=20)
    assert len(out) <= 20
    assert not out.endswith("_")


def test_slugify_falls_back_when_empty():
    assert slugify(None, fallback="abc123") == "abc123"
    assert slugify("!!! ---", fallback="abc123") == "abc123"
