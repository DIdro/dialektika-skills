"""Телеграм: один пинг со ссылкой на дашборд. Разбор живёт в вебе.

Карточка со свайпами и 🧭-вопрос удалены: чат-поверхность не годится для
восемнадцати атомов по трём проектам, а свободнотекстовый ответ на 🧭 терялся
молча (так уже было: звонок с 16 задачами исчез без следа).
"""
import logging
import time

_log = logging.getLogger(__name__)

COOLDOWN_SEC = 3600
_KV_KEY = "last_ping_ts"


# Статусы, при которых строка всё ещё ждёт владельца. reparsing — тоже ожидание:
# откат статуса в awaiting живёт в обработчике исключения review_actions.reparse
# и не переживает убийства процесса, поэтому строка может застрять в reparsing
# навсегда. Не посчитать её здесь — значит потерять её молча: вернуть застрявшую
# строку можно только руками из интерфейса, а до него надо сперва узнать, что она есть.
PENDING_STATUSES = ("awaiting", "reparsing")


def ping_pending(cfg, bot, store) -> None:
    pending = [r for r in store.list_queue() if r["status"] in PENDING_STATUSES]
    if not pending:
        return
    last = store.get_kv(_KV_KEY)
    now = time.time()
    if last and now - float(last) < COOLDOWN_SEC:
        return
    text = (f"🎧 {len(pending)} звонк(ов) ждут разбора\n{cfg.dashboard_url}")
    try:
        bot.send_message(cfg.owner_chat_id, text)
        store.set_kv(_KV_KEY, str(now))
    except Exception:
        _log.exception("ping failed")
