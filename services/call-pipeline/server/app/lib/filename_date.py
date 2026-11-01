"""Извлечение даты записи (recorded_at) из имени файла. Stdlib-only, тестируется без FastAPI.

Покрывает:
  telemost_20260619_143000_1234.ogg   → 2026-06-19  (рекордер Telemost)
  zoom_call_2026-06-19.webm           → 2026-06-19
  Запись 2026-06-19 14-30.opus        → 2026-06-19
  20260619_meeting.m4a                → 2026-06-19
Возвращает ISO-дату 'YYYY-MM-DD' или None.
"""
import re
from datetime import date

# порядок важен: сперва datetime-форма с временем (рекордер), затем ISO, затем голые 8 цифр
_PATTERNS = [
    re.compile(r"(\d{4})(\d{2})(\d{2})[_\-]\d{6}"),   # 20260619_143000
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),           # 2026-06-19
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),  # 20260619 (не часть длинной цифры)
]


def parse_recorded_at(filename) -> str | None:
    if not filename:
        return None
    for pat in _PATTERNS:
        m = pat.search(filename)
        if not m:
            continue
        y, mo, d = m.group(1), m.group(2), m.group(3)
        try:
            date(int(y), int(mo), int(d))      # валидация
        except ValueError:
            continue
        return f"{y}-{mo}-{d}"
    return None
