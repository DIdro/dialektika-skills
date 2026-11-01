from ops_ingest import alerts
from ops_ingest.store import Store


class _Cfg:
    owner_chat_id = "1"


class _Bot:
    def __init__(self, boom=False):
        self.sent = []
        self.boom = boom

    def send_message(self, chat_id, text, **kw):
        if self.boom:
            raise RuntimeError("телеграм недоступен")
        self.sent.append(text)
        return {"result": {"message_id": len(self.sent)}}


def _store(tmp_path):
    return Store(str(tmp_path / "s.db"))


class _Credits(Exception):
    pass


def test_classify_distinguishes_the_three_classes():
    assert alerts.classify(_Credits("Error code: 400 - Your credit balance is too low")) == "credits"
    assert alerts.classify(RuntimeError("401 Unauthorized")) == "auth"
    assert alerts.classify(ValueError("что-то пошло не так")) == "other"


def test_credits_alert_fires_immediately_without_streak(tmp_path):
    """Кончившиеся кредиты — беда всего потока, а не одного звонка:
    ждать серии падений неправильно, разбор уже стоит."""
    s, bot = _store(tmp_path), _Bot()
    assert alerts.job_failed(_Cfg(), bot, s, "j1", _Credits("credit balance is too low")) is True
    assert len(bot.sent) == 1
    assert "кредиты" in bot.sent[0].lower()


def test_credits_alert_is_not_repeated_every_cycle(tmp_path):
    """Поллер ходит раз в минуту: без кулдауна это 1440 сообщений в сутки."""
    s, bot = _store(tmp_path), _Bot()
    for _ in range(5):
        alerts.job_failed(_Cfg(), bot, s, "j1", _Credits("credit balance is too low"))
    assert len(bot.sent) == 1


def test_credits_alert_not_tied_to_one_job(tmp_path):
    """Кредиты кончились для всех — второй звонок не должен давать второе письмо."""
    s, bot = _store(tmp_path), _Bot()
    alerts.job_failed(_Cfg(), bot, s, "j1", _Credits("credit balance is too low"))
    alerts.job_failed(_Cfg(), bot, s, "j2", _Credits("credit balance is too low"))
    assert len(bot.sent) == 1


def test_single_job_failure_is_silent_until_streak(tmp_path):
    """Разовый сбой сети — не новость. Сигнал только когда звонок застрял."""
    s, bot = _store(tmp_path), _Bot()
    assert alerts.job_failed(_Cfg(), bot, s, "j1", ValueError("сеть моргнула")) is False
    assert alerts.job_failed(_Cfg(), bot, s, "j1", ValueError("сеть моргнула")) is False
    assert bot.sent == []
    assert alerts.job_failed(_Cfg(), bot, s, "j1", ValueError("сеть моргнула")) is True
    assert len(bot.sent) == 1
    assert "j1" in bot.sent[0]


def test_streak_is_per_job(tmp_path):
    """Падения разных звонков не складываются в одну серию."""
    s, bot = _store(tmp_path), _Bot()
    for jid in ("j1", "j2", "j3"):
        alerts.job_failed(_Cfg(), bot, s, jid, ValueError("сбой"))
    assert bot.sent == []


def test_recovery_resets_the_streak(tmp_path):
    """Иначе один давний сбой навсегда держит счётчик у порога, и следующее
    единичное падение выглядит как застрявший звонок."""
    s, bot = _store(tmp_path), _Bot()
    alerts.job_failed(_Cfg(), bot, s, "j1", ValueError("сбой"))
    alerts.job_failed(_Cfg(), bot, s, "j1", ValueError("сбой"))
    alerts.job_recovered(s, "j1")
    assert alerts.job_failed(_Cfg(), bot, s, "j1", ValueError("сбой")) is False
    assert bot.sent == []


def test_broken_telegram_does_not_break_the_poller(tmp_path):
    """Сигнализация, роняющая поллер, хуже отсутствующей сигнализации."""
    s, bot = _store(tmp_path), _Bot(boom=True)
    assert alerts.job_failed(_Cfg(), bot, s, "j1", _Credits("credit balance is too low")) is False


def test_failed_send_does_not_burn_the_cooldown(tmp_path):
    """Если отправка не удалась, кулдаун не ставится — иначе единственная
    неудачная попытка проглотила бы сигнал на шесть часов."""
    s = _store(tmp_path)
    alerts.job_failed(_Cfg(), _Bot(boom=True), s, "j1", _Credits("credit balance is too low"))
    good = _Bot()
    assert alerts.job_failed(_Cfg(), good, s, "j1", _Credits("credit balance is too low")) is True
    assert len(good.sent) == 1


def test_drill_is_marked_as_a_drill(tmp_path):
    """Проверочный сигнал обязан быть отличим от боевого.

    Так уже было: : проверка канала пришла дословно как настоящая тревога про
    кончившиеся кредиты, владелец пошёл проверять баланс — а баланс был в порядке.
    """
    s, bot = _store(tmp_path), _Bot()
    alerts.job_failed(_Cfg(), bot, s, "j1", _Credits("credit balance is too low"), drill=True)
    assert bot.sent[0].startswith("🧪")
    assert "УЧЕБНАЯ" in bot.sent[0]


def test_real_alert_has_no_drill_prefix(tmp_path):
    s, bot = _store(tmp_path), _Bot()
    alerts.job_failed(_Cfg(), bot, s, "j1", _Credits("credit balance is too low"))
    assert not bot.sent[0].startswith("🧪")
