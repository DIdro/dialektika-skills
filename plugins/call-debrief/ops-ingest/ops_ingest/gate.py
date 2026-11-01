"""Пять стопов гейта как флаги строки очереди.

Гейт НЕ решает судьбу атомов — решение принимает владелец в дашборде. Флаги красят
строку, поднимают её вверх и объясняют, на что смотреть. Каждый стоп меряется
отдельно: общий балл при дырявом покрытии уже подводил на золотом наборе
другого нашего агента.
"""
import re

# Порог уверенности классификатора. Замер на золотом наборе
# (наш внутренний прогон): верная маршрутизация даёт 0.75,
# мисроуты — 0.30 / 0.55 / 0.60. Порог 0.7 их разделяет.
CONFIDENCE_MIN = 0.7

# Доля сегментов без спикер-метки, выше которой атрибуции нельзя верить.
UNKNOWN_SHARE_MAX = 0.3

# Происхождение, само по себе подозрительное на присланный материал.
# Downloads НЕ входит: туда падают и скачанные Zoom-записи владельца.
FOREIGN_ORIGINS = {"telegram-desktop", "unknown"}

# Квантификатор {2,} был заменён на * (Task 11, находка ревью Important):
# при {2,} короткие суммы вида «50к» не матчились — а это самая частая форма
# записи денег в разговоре. Компромисс осознанный: короткая форма даёт редкие
# ложные срабатывания вроде «3 к 1», это принято намеренно — флаг ничего не
# блокирует, он только красит строку, поэтому пропущенное обязательство
# дороже лишнего флага.
_MONEY_RE = re.compile(
    r"\d[\d\s  ]*\s*(?:руб|₽|р\.|тыс|млн|k|к\b)|\$\s*\d|\d+\s*(?:тыс|млн)\b",
    re.IGNORECASE)


def _has_stakes(commitments) -> bool:
    """Обязательство владельца с датой или суммой — факт о внешнем мире,
    цена ошибки высокая."""
    for item in (commitments.by_owner if commitments else []):
        if item.deadline:
            return True
        if _MONEY_RE.search(item.what or ""):
            return True
    return False


def evaluate(draft, facts, origin) -> list[str]:
    flags: list[str] = []

    proj = draft.primary_project
    if proj is None or proj.confidence is None or proj.confidence < CONFIDENCE_MIN:
        flags.append("project_unclear")

    if _has_stakes(draft.commitments):
        flags.append("commitment_with_stakes")

    if draft.owner_identified is False or (facts.get("unknown_share") or 0) > UNKNOWN_SHARE_MAX:
        flags.append("attribution_unclear")

    if (origin or "unknown") in FOREIGN_ORIGINS:
        flags.append("foreign_recording")

    return flags
