"""Сигнализация о поломках разбора в телеграм.

До этого модуля любой сбой уходил только в журнал: строка `_log.exception` в
`tick`, и всё. Практика 07.09 показала цену такого молчания — за один день
поток вставал трижды (дважды на баге схемы драфта, один раз на кончившихся
кредитах Anthropic), и каждый раз это замечалось случайно, при чтении логов.
В обычный день звонки просто перестали бы разбираться, тихо.

Три правила, ради которых модуль отдельный, а не пара строк в `tick`:

1. **Классификация важнее текста ошибки.** «Кончились кредиты» и «упал один
   звонок» — разные события с разной срочностью и разным действием владельца.
   Одинаковое сообщение на оба приучает игнорировать оба.
2. **Кулдаун на класс, а не на сообщение.** Поллер ходит раз в минуту: без
   кулдауна кончившиеся кредиты дали бы 1440 сообщений в сутки, и канал
   перестал бы работать как канал.
3. **Об одном звонке — только когда он застрял.** Разовый сбой сети не повод
   писать; повод — когда звонок падает раз за разом и не разбирается вовсе.
"""
import logging
import time

_log = logging.getLogger(__name__)

# Порог, после которого падение конкретного звонка перестаёт быть случайностью.
FAIL_STREAK_ALERT = 3

# Кулдаун на класс события, секунды. Кредиты — раз в шесть часов: чаще
# бессмысленно, владелец не пополнит баланс быстрее оттого, что ему написали
# двадцать раз. Остальное — раз в час.
COOLDOWN_SEC = {"credits": 6 * 3600, "auth": 3600, "job": 24 * 3600, "other": 3600}

_KIND_TEXT = {
    "credits": "🔴 Разбор звонков ОСТАНОВЛЕН: кончились кредиты Anthropic.\n"
               "Звонки копятся необработанными. Пополни баланс — дальше поллер "
               "подхватит их сам.",
    "auth": "🔴 Разбор звонков остановлен: Мионика не принимает токен.\n"
            "Похоже, он протух — нужно перевыпустить.",
}


def classify(exc: Exception) -> str:
    """Класс поломки по тексту исключения.

    По тексту, а не по типу: и кредиты, и протухший токен приезжают одним и тем
    же `BadRequestError`/`APIStatusError`, различает их только сообщение.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if "credit balance" in text or "insufficient" in text or "billing" in text:
        return "credits"
    if "unauthorized" in text or "401" in text or "invalid token" in text or "expired" in text:
        return "auth"
    return "other"


def _cooled_down(store, key: str, seconds: int) -> bool:
    """True, если по этому ключу писать ещё рано."""
    last = store.get_kv(key)
    if not last:
        return False
    try:
        return (time.time() - float(last)) < seconds
    except (TypeError, ValueError):
        return False


# Префикс учебной тревоги. Так уже было: : проверочный сигнал про кончившиеся
# кредиты пришёл владельцу дословно как боевой — он пошёл проверять баланс,
# а баланс был в порядке. Тревога, неотличимая от настоящей, обесценивает канал
# быстрее, чем молчание.
DRILL_PREFIX = "🧪 УЧЕБНАЯ ТРЕВОГА (проверка канала, реагировать не надо)\n\n"


def _send(cfg, bot, store, key: str, text: str, seconds: int, drill: bool = False) -> bool:
    if drill:
        text = DRILL_PREFIX + text
    if _cooled_down(store, key, seconds):
        return False
    try:
        bot.send_message(cfg.owner_chat_id, text)
    except Exception:
        # Сигнализация, которая роняет поллер, хуже отсутствующей сигнализации.
        _log.exception("не удалось отправить сигнал %s", key)
        return False
    store.set_kv(key, str(time.time()))
    return True


def job_failed(cfg, bot, store, job_id: str, exc: Exception, drill: bool = False) -> bool:
    """Звонок не разобрался. Вернёт True, если сигнал ушёл.

    Кредиты и протухший токен — беда всего потока, а не одного звонка: пишем
    сразу, не дожидаясь серии, и не привязываем к job_id. Прочее — только когда
    звонок падает подряд FAIL_STREAK_ALERT раз: разовый сбой сети не новость.
    """
    kind = classify(exc)
    if kind in ("credits", "auth"):
        return _send(cfg, bot, store, f"alert:{kind}", _KIND_TEXT[kind],
                     COOLDOWN_SEC[kind], drill=drill)

    streak_key = f"failstreak:{job_id}"
    try:
        streak = int(store.get_kv(streak_key) or 0) + 1
    except (TypeError, ValueError):
        streak = 1
    store.set_kv(streak_key, str(streak))
    if streak < FAIL_STREAK_ALERT:
        return False

    text = (f"⚠️ Звонок не разбирается: {job_id[:8]}\n"
            f"Попыток подряд: {streak}. Ошибка: {type(exc).__name__}: {str(exc)[:200]}\n"
            f"Поллер продолжает пробовать его каждый цикл — если это надолго, "
            f"звонок стоит убрать из инбокса.")
    return _send(cfg, bot, store, f"alert:job:{job_id}", text, COOLDOWN_SEC["job"], drill=drill)


def job_recovered(store, job_id: str) -> None:
    """Звонок разобрался — серия падений сброшена.

    Без сброса один давний сбой навсегда держал бы счётчик у порога, и следующее
    единичное падение выглядело бы как застрявший звонок.
    """
    store.set_kv(f"failstreak:{job_id}", "0")
