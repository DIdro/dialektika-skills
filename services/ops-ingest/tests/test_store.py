from ops_ingest.store import Store
import json


# ── очередь приёмки (веб-ревью) ─────────────────────────────────────────────

def test_queue_roundtrip(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", source="call:j1", summary="итоги", count=2,
                       project="demo-bot", confidence=0.82,
                       tasks=[{"title": "написать тезисы"}],
                       atoms=[{"kind": "note", "text": "а"}],
                       origin="zoom", gate_flags=["project_unclear"],
                       diarized=True, speaker_count=3, unknown_share=0.1,
                       owner_identified=True, recorded_at="2026-09-01", status="awaiting")
    row = s.get_queue_row("j1")
    assert row["project"] == "demo-bot"
    assert row["atoms"] == [{"kind": "note", "text": "а"}]
    assert row["gate_flags"] == ["project_unclear"]
    assert row["diarized"] is True
    assert row["owner_identified"] is True
    assert row["status"] == "awaiting"


def test_upsert_updates_only_given_fields(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", summary="итоги", project="demo-bot", status="awaiting")
    s.upsert_queue_row("j1", project="aaa-demo")
    row = s.get_queue_row("j1")
    assert row["project"] == "aaa-demo"
    assert row["summary"] == "итоги"      # не затёрлось


def test_list_queue_filters_by_status(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", status="awaiting")
    s.upsert_queue_row("j2", status="accepted")
    assert [r["job_id"] for r in s.list_queue(status="awaiting")] == ["j1"]
    assert len(s.list_queue()) == 2


def test_upsert_partial_update_preserves_json_and_bool_fields(tmp_path):
    """Частичный upsert (одно поле status) не должен затирать JSON- и булевы поля —
    следующие задачи меняют по одному полю за раз и рассчитывают, что остальное уцелеет."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", summary="итоги", tasks=[{"title": "x"}],
                       atoms=[{"kind": "note", "text": "а"}],
                       gate_flags=["project_unclear"],
                       diarized=True, owner_identified=False, status="awaiting")
    s.upsert_queue_row("j1", status="accepted")
    row = s.get_queue_row("j1")
    assert row["summary"] == "итоги"
    assert row["tasks"] == [{"title": "x"}]
    assert row["atoms"] == [{"kind": "note", "text": "а"}]
    assert row["gate_flags"] == ["project_unclear"]
    assert row["diarized"] is True
    assert row["owner_identified"] is False     # не True и не None — именно False уцелело
    assert row["status"] == "accepted"


def test_owner_identified_none_when_never_set(tmp_path):
    """None ≠ False: None — разметки диаризации не было вовсе, False — была, но владельца
    не опознали. Строка без owner_identified обязана вернуть None, а не False."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", summary="итоги", status="awaiting")
    row = s.get_queue_row("j1")
    assert row["owner_identified"] is None


def test_alternatives_and_uncertainty_roundtrip(tmp_path):
    """Альтернативы классификатора и текст неуверенности живут в строке очереди
    рядом с выбором — страница «Разбор» показывает их при низкой уверенности."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", project="demo-bot", confidence=0.55,
                       alternatives=[{"key": "demo-crm", "confidence": 0.3}],
                       uncertainty="непонятно, чей это проект", status="awaiting")
    row = s.get_queue_row("j1")
    assert row["alternatives"] == [{"key": "demo-crm", "confidence": 0.3}]
    assert row["uncertainty"] == "непонятно, чей это проект"


def test_alternatives_default_to_empty_list_on_old_rows(tmp_path):
    """У строки, записанной до появления колонки, alternatives — пустой список,
    а не None: страница не должна падать на старых данных."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", summary="итоги", status="awaiting")
    row = s.get_queue_row("j1")
    assert row["alternatives"] == []
    assert row["uncertainty"] is None


def test_legacy_rows_without_atoms_leave_the_awaiting_queue(tmp_path):
    """Строка из ПРЕЖНЕГО потока (материал уже в графе, atoms_json не заполнена)
    не должна попасть в выдачу ожидающих: приёмка записала бы её второй раз."""
    db = str(tmp_path / "s.db")
    s = Store(db)
    s.upsert_queue_row("old", summary="разобрано ботом", status="awaiting")   # atoms не задавали
    s.upsert_queue_row("new", summary="свежий", atoms=[], status="awaiting")  # пустой список — это НЕ legacy
    assert {r["job_id"] for r in s.list_queue(status="awaiting")} == {"old", "new"}

    s2 = Store(db)   # переоткрытие = прогон миграций
    assert [r["job_id"] for r in s2.list_queue(status="awaiting")] == ["new"]
    assert s2.get_queue_row("old")["status"] == "legacy"

    # идемпотентность: повторный прогон ничего не трогает
    s3 = Store(db)
    assert [r["job_id"] for r in s3.list_queue(status="awaiting")] == ["new"]
    assert s3.get_queue_row("old")["status"] == "legacy"


def test_queue_metrics_counts_rework_and_flags(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="accepted", gate_flags=["project_unclear"],
                       decided_at="2026-09-06T10:00:00+00:00")
    s.upsert_queue_row("b", status="rejected", gate_flags=[],
                       decided_at="2026-09-06T10:00:00+00:00")
    s.upsert_queue_row("c", status="awaiting", gate_flags=["foreign_recording",
                                                           "project_unclear"])
    m = s.queue_metrics()
    assert m["awaiting"] == 1
    assert m["reworked_share"] == 0.5              # 1 отклонён из 2 решённых
    assert m["flag_counts"]["project_unclear"] == 2
    assert m["flag_counts"]["foreign_recording"] == 1


def test_queue_metrics_excludes_split_from_reworked_share(tmp_path):
    """split — не решение о качестве разбора, а расщепление строки на потомков
    (каждый потомок будет принят/отклонён отдельно). Если её засчитать как решённую
    и не-принятую, reworked_share будет систематически завышен.

    В наборе три строки (rejected + split + accepted), а не две — чтобы числитель
    и знаменатель не могли совпасть по случайности при неверной реализации: без
    accepted-строки reworked_share == 1.0 получился бы и при правильном исключении
    split (1 отклонена из 1 решённой), и при ошибке, где split ошибочно засчитан
    как ещё одна "неудача" (2 из 2) — оба варианта неотличимы. С accepted-строкой
    правильный ответ (0.5) отличается от обоих вариантов ошибки."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="rejected", gate_flags=[],
                       decided_at="2026-09-06T10:00:00+00:00")
    s.upsert_queue_row("b", status="split", gate_flags=["project_unclear"],
                       decided_at="2026-09-06T10:00:00+00:00")
    s.upsert_queue_row("c", status="accepted", gate_flags=[],
                       decided_at="2026-09-06T10:00:00+00:00")
    m = s.queue_metrics()
    # без "b" в знаменателе решённых — "a" и "c": 1 отклонён из 2 решённых
    assert m["reworked_share"] == 0.5
    assert m["awaiting"] == 0            # split — не awaiting и не решённая
    assert m["flag_counts"]["project_unclear"] == 1   # флаги split всё равно считаются


def test_queue_metrics_median_odd_count(tmp_path):
    """Медиана — средний по возрастанию элемент задержки "запись → решение" при
    нечётном числе решённых строк. Задержка считается от recorded_at (дата записи
    звонка) до decided_at (момент решения) — проверяю на конкретном числе минут,
    а не просто на "не пусто"."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="accepted", recorded_at="2026-09-06T10:00:00+00:00",
                       decided_at="2026-09-06T10:05:00+00:00")   # 5 минут
    s.upsert_queue_row("b", status="rejected", recorded_at="2026-09-06T10:00:00+00:00",
                       decided_at="2026-09-06T10:25:00+00:00")   # 25 минут
    s.upsert_queue_row("c", status="accepted", recorded_at="2026-09-06T10:00:00+00:00",
                       decided_at="2026-09-06T10:15:00+00:00")   # 15 минут
    m = s.queue_metrics()
    assert m["median_minutes_to_decision"] == 15.0   # средний из отсортированных [5, 15, 25]


def test_queue_metrics_median_even_count(tmp_path):
    """Чётное число решённых строк: формула `deltas[len(deltas) // 2]` берёт ВЕРХНИЙ
    из двух центральных элементов, а не их среднее арифметическое — так заложено в
    самом плане задачи (Task 19), это не баг реализации. Для рабочего пульта точность
    до минуты не критична; фиксирую фактическое поведение явно, чтобы следующий
    читатель не принял его за дефект и не "исправил" вопреки плану."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="accepted", recorded_at="2026-09-06T10:00:00+00:00",
                       decided_at="2026-09-06T10:10:00+00:00")   # 10 минут
    s.upsert_queue_row("b", status="rejected", recorded_at="2026-09-06T10:00:00+00:00",
                       decided_at="2026-09-06T10:20:00+00:00")   # 20 минут
    m = s.queue_metrics()
    assert m["median_minutes_to_decision"] == 20.0   # верхний из двух средних [10, 20]


def test_queue_metrics_median_none_without_dates(tmp_path):
    """Ни у одной решённой строки нет recorded_at — список задержек пуст, медиана
    None, метод не падает; остальные метрики при этом считаются как обычно."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="accepted", decided_at="2026-09-06T10:00:00+00:00")
    s.upsert_queue_row("b", status="rejected", decided_at="2026-09-06T10:00:00+00:00")
    m = s.queue_metrics()
    assert m["median_minutes_to_decision"] is None
    assert m["reworked_share"] == 0.5


def test_queue_metrics_median_skips_unparseable_date(tmp_path):
    """Строка с нечитаемой датой не роняет весь расчёт — просто выпадает из выборки
    задержек (перехват ValueError в queue_metrics), остальные решённые строки
    считаются как обычно."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="accepted", recorded_at="не дата",
                       decided_at="2026-09-06T10:05:00+00:00")
    s.upsert_queue_row("b", status="rejected", recorded_at="2026-09-06T10:00:00+00:00",
                       decided_at="2026-09-06T10:15:00+00:00")   # 15 минут — единственная валидная
    m = s.queue_metrics()
    assert m["median_minutes_to_decision"] == 15.0


def test_queue_metrics_reworked_share_zero_when_nothing_decided(tmp_path):
    """Все строки ещё ждут решения — решённых нет вовсе. Пустой знаменатель
    (len(decided) == 0) защищён в коде явно тернарником; план прямо требует
    гарантии для этого случая — без этого теста регресс тут не поймать."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("a", status="awaiting", gate_flags=[])
    s.upsert_queue_row("b", status="awaiting", gate_flags=["project_unclear"])
    m = s.queue_metrics()
    assert m["reworked_share"] == 0.0
    assert m["awaiting"] == 2
    assert m["median_minutes_to_decision"] is None


def test_mark_and_check_processed(tmp_path):
    s = Store(str(tmp_path / "state.db"))
    assert s.is_processed("J1") is False
    s.mark_processed("J1", "транскрипт текст")
    assert s.is_processed("J1") is True


def test_mark_processed_idempotent(tmp_path):
    s = Store(str(tmp_path / "state.db"))
    s.mark_processed("J1", "a")
    s.mark_processed("J1", "a")          # повторный вызов не падает
    assert s.is_processed("J1") is True


def test_get_transcript(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    assert s.get_transcript("J1") is None
    s.mark_processed("J1", "полный транскрипт звонка")
    assert s.get_transcript("J1") == "полный транскрипт звонка"


def test_kv_roundtrip(tmp_path):
    """get_kv/set_kv — ими пользуется notifier.ping_pending для кулдауна."""
    s = Store(str(tmp_path / "s.db"))
    assert s.get_kv("last_ping_ts") is None
    s.set_kv("last_ping_ts", "123.0")
    assert s.get_kv("last_ping_ts") == "123.0"
    s.set_kv("last_ping_ts", "456.0")
    assert s.get_kv("last_ping_ts") == "456.0"


def test_processed_keys(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    assert s.is_key_processed("abc") is False
    s.mark_key_processed("abc")
    assert s.is_key_processed("abc") is True
    s.mark_key_processed("abc")   # идемпотентно, не падает
    assert s.is_key_processed("abc") is True


def test_queue_metrics_survives_mixed_timezone_awareness(tmp_path):
    """В одной колонке соседствуют даты с зоной и без.

    `decided_at` пишется приложением с зоной, `recorded_at` — голая дата
    звонка, ручные правки через sqlite дают третий вид. Так уже было: :
    вычитание разнородных дат роняло весь эндпоинт метрик пятисоткой.
    """
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("aware", status="accepted", gate_flags=[],
                       recorded_at="2026-09-06", decided_at="2026-09-06T12:00:00+00:00")
    s.upsert_queue_row("naive", status="accepted", gate_flags=[],
                       recorded_at="2026-09-06", decided_at="2026-09-06 13:00:00")
    m = s.queue_metrics()
    assert m["median_minutes_to_decision"] in (720.0, 780.0)
    assert m["reworked_share"] == 0.0


def test_queue_row_carries_ts_as_date_fallback(tmp_path):
    """У звонков без даты в имени файла recorded_at пуст — витрине нужен ts,
    иначе колонка «Дата» в разборе пустая и свежий звонок в очереди не найти."""
    s = Store(str(tmp_path / "s.db"))
    s.upsert_queue_row("j1", summary="без даты в имени", recorded_at=None)
    row = s.get_queue_row("j1")
    assert row["recorded_at"] is None
    assert row["ts"]
    assert s.list_queue()[0]["ts"] == row["ts"]


def test_ts_is_not_writable_through_upsert(tmp_path):
    """ts проставляет база; белый список на запись он расширять не должен."""
    import pytest
    s = Store(str(tmp_path / "s.db"))
    with pytest.raises(ValueError):
        s.upsert_queue_row("j1", ts="2020-01-01")
