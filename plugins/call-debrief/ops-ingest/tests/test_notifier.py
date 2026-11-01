from ops_ingest.notifier import ping_pending
from ops_ingest.store import Store


class _Cfg:
    owner_chat_id = "1"
    dashboard_url = "https://example.com/review"


class _Bot:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return {"result": {"message_id": len(self.sent)}}


def test_ping_names_count_and_link(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", status="awaiting")
    s.upsert_queue_row("j2", status="awaiting")
    s.upsert_queue_row("j3", status="accepted")
    bot = _Bot()
    ping_pending(_Cfg(), bot, s)
    assert len(bot.sent) == 1
    assert "2" in bot.sent[0]
    assert "https://example.com/review" in bot.sent[0]


def test_ping_counts_rows_stuck_in_reparsing(tmp_path):
    """Строка, застрявшая в reparsing (процесс убит до отката статуса), не видна
    ни в виде по умолчанию, ни в метриках — если и пинг её не считает, она
    потеряна молча. Считаем её наравне с ожидающими."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", atoms=[], status="awaiting")
    s.upsert_queue_row("j2", atoms=[], status="reparsing")
    s.upsert_queue_row("j3", atoms=[], status="accepted")
    bot = _Bot()
    ping_pending(_Cfg(), bot, s)
    assert len(bot.sent) == 1
    assert "2" in bot.sent[0]


def test_ping_fires_when_only_a_stuck_row_remains(tmp_path):
    """Одна застрявшая строка — уже повод пингануть: иначе её никто не увидит."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", atoms=[], status="reparsing")
    bot = _Bot()
    ping_pending(_Cfg(), bot, s)
    assert len(bot.sent) == 1


def test_ping_is_silent_when_queue_empty(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    bot = _Bot()
    ping_pending(_Cfg(), bot, s)
    assert bot.sent == []


def test_ping_respects_cooldown(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", status="awaiting")
    bot = _Bot()
    ping_pending(_Cfg(), bot, s)
    ping_pending(_Cfg(), bot, s)
    assert len(bot.sent) == 1
