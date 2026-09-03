import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_corpus import corpus_filename, render_post

REC = {"id": 342, "date": "2025-11-14T10:00:00+00:00", "text": "Первая строка.\n\nВторая."}


def test_filename_is_date_and_id():
    assert corpus_filename(REC) == "2025-11-14-342.md"


def test_render_has_frontmatter():
    out = render_post(REC)
    assert out.startswith("---\n")
    assert "date: 2025-11-14" in out
    assert "id: 342" in out
    assert "genre: unlabeled" in out


def test_render_keeps_text_verbatim():
    out = render_post(REC)
    assert out.endswith("Первая строка.\n\nВторая.\n")


def test_render_preserves_emoji_and_dashes():
    rec = dict(REC, text="Мысль — вот такая 🙂")
    assert "Мысль — вот такая 🙂" in render_post(rec)
