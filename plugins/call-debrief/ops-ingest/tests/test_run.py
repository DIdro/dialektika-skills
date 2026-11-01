import asyncio
from ops_ingest import run as run_mod
from ops_ingest.store import Store
from ops_ingest.draft_schema import Draft, Commitments, CommitmentItem, ProjectGuess


def _cfg(tmp_path):
    return run_mod.Config(cp_base="http://cp", cp_token="t", mionika_token="m",
                          anthropic_key="k", db_path=str(tmp_path / "s.db"),
                          bot_token="b", owner_chat_id="1", skip_after_process=False,
                          dashboard_url="http://dash/review", owner_user_id="u-owner")


def _draft(conf=0.9):
    return Draft(summary="итоги",
                 primary_project=ProjectGuess(key="demo-bot", confidence=conf, rationale="r"),
                 commitments=Commitments(by_owner=[CommitmentItem(what="Задача 1")]))


class _Bot:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return {"result": {"message_id": len(self.sent)}}


def test_process_job_queues_draft_and_writes_nothing_to_graph(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(run_mod.cp, "get_job",
                        lambda *a: {"recorded_at": "2026-09-01", "origin": "zoom",
                                    "audio_duration_sec": 600})
    monkeypatch.setattr(run_mod.cp, "get_transcript_payload", lambda *a: {
        "diarized": True, "text": "плоский",
        "segments": [{"speaker": "SPEAKER_00", "text": "а"},
                     {"speaker": "SPEAKER_01", "text": "б"}]})
    monkeypatch.setattr(run_mod.cp, "render_transcript", lambda p: "[SPEAKER_00] а")

    async def fake_projects(tok):
        return [{"key": "demo-bot", "name": "Demo Bot"}]

    async def fake_classify(text, projects, key, **kw):
        return _draft(conf=0.9)

    async def fake_extract(text, key, **kw):
        calls.append("extract")
        return {"atoms": [{"kind": "note", "text": "атом"}], "recency": {"is_stale": False}}

    def boom(*a, **kw):
        raise AssertionError("run.py не имеет права писать в Мионику")

    monkeypatch.setattr(run_mod.file_backend.mio, "fetch_projects", fake_projects)
    monkeypatch.setattr(run_mod.file_backend.mio, "write_proposed", boom)
    monkeypatch.setattr(run_mod, "classify", fake_classify)
    monkeypatch.setattr(run_mod.cu, "extract_atoms", fake_extract)

    cfg = _cfg(tmp_path)
    store = Store(cfg.db_path)
    asyncio.run(run_mod.process_job(cfg, _Bot(), store, "j1"))

    row = store.get_queue_row("j1")
    assert row["status"] == "awaiting"
    assert row["project"] == "demo-bot"
    assert row["atoms"] == [{"kind": "note", "text": "атом"}]
    assert row["origin"] == "zoom"
    assert row["diarized"] is True
    assert row["speaker_count"] == 2
    assert row["gate_flags"] == []
    assert calls == ["extract"]
    # своя копия — ОТРЕНДЕРЕННЫЙ транскрипт (с разметкой спикеров через render_transcript),
    # а не сырой payload: reparse читает именно её и идёт в call-pipeline только при промахе.
    assert store.get_transcript("j1") == "[SPEAKER_00] а"


def test_process_job_records_gate_flags(tmp_path, monkeypatch):
    """Чужая запись и низкая уверенность поднимают флаги, но поток не останавливают."""
    monkeypatch.setattr(run_mod.cp, "get_job",
                        lambda *a: {"recorded_at": "2026-09-01",
                                    "origin": "telegram-desktop",
                                    "audio_duration_sec": 600})
    monkeypatch.setattr(run_mod.cp, "get_transcript_payload", lambda *a: {
        "diarized": True, "text": "плоский",
        "segments": [{"speaker": "SPEAKER_00", "text": "а"},
                     {"speaker": "SPEAKER_01", "text": "б"}]})
    monkeypatch.setattr(run_mod.cp, "render_transcript", lambda p: "[SPEAKER_00] а")

    async def fake_projects(tok):
        return [{"key": "demo-bot", "name": "Demo Bot"}]

    async def fake_classify(text, projects, key, **kw):
        return _draft(conf=0.4)

    async def fake_extract(text, key, **kw):
        return {"atoms": [{"kind": "note", "text": "атом"}], "recency": {"is_stale": False}}

    def boom(*a, **kw):
        raise AssertionError("run.py не имеет права писать в Мионику")

    monkeypatch.setattr(run_mod.file_backend.mio, "fetch_projects", fake_projects)
    monkeypatch.setattr(run_mod.file_backend.mio, "write_proposed", boom)
    monkeypatch.setattr(run_mod, "classify", fake_classify)
    monkeypatch.setattr(run_mod.cu, "extract_atoms", fake_extract)

    cfg = _cfg(tmp_path)
    store = Store(cfg.db_path)
    asyncio.run(run_mod.process_job(cfg, _Bot(), store, "j1"))

    row = store.get_queue_row("j1")
    assert set(row["gate_flags"]) == {"project_unclear", "foreign_recording"}
    assert row["status"] == "awaiting"     # флаги красят строку, а не останавливают поток


def test_process_job_early_skip_when_key_processed(tmp_path, monkeypatch):
    """Повторная присылка того же разговора (recording-key уже помечен обработанным
    под другим job_id) — ранний скип: своя копия транскрипта сохраняется, но
    классификатор/извлечение/граф не трогаются, а job подчищается из call-pipeline,
    если включён skip_after_process."""
    monkeypatch.setattr(run_mod.cp, "get_job",
                        lambda *a: {"dedupe_key": "sha1:short", "transcript_text": "t"})
    deleted = []
    monkeypatch.setattr(run_mod.cp, "delete_job", lambda *a: deleted.append(a))

    def boom(*a, **kw):
        raise AssertionError("ранний скип не должен трогать классификатор/граф")

    monkeypatch.setattr(run_mod.file_backend.mio, "fetch_projects", boom)
    monkeypatch.setattr(run_mod, "classify", boom)
    monkeypatch.setattr(run_mod.cu, "extract_atoms", boom)
    monkeypatch.setattr(run_mod.file_backend.mio, "write_proposed", boom)

    cfg = run_mod.Config(cp_base="http://cp", cp_token="t", mionika_token="m",
                         anthropic_key="k", db_path=str(tmp_path / "s.db"),
                         bot_token="b", owner_chat_id="1", skip_after_process=True,
                         dashboard_url="http://dash/review", owner_user_id="u-owner")
    store = Store(cfg.db_path)
    store.mark_key_processed("sha1")

    n = asyncio.run(run_mod.process_job(cfg, _Bot(), store, "j1"))

    assert n == 0
    assert store.is_processed("j1") is True
    assert store.get_queue_row("j1") is None        # в очередь не попал
    assert deleted == [("http://cp", "t", "j1")]     # job вычищен из call-pipeline


def test_process_job_marks_key_after_normal_flow(tmp_path, monkeypatch):
    """После обычного разбора recording-key звонка помечается обработанным — иначе
    повторная присылка того же разговора не попадёт в ранний скип и продублирует
    строку очереди."""
    monkeypatch.setattr(run_mod.cp, "get_job",
                        lambda *a: {"dedupe_key": "sha2:call", "recorded_at": "2026-08-01",
                                    "origin": "zoom"})
    monkeypatch.setattr(run_mod.cp, "get_transcript_payload", lambda *a: {
        "diarized": False, "text": "текст", "segments": []})
    monkeypatch.setattr(run_mod.cp, "render_transcript", lambda p: p["text"])

    async def fake_projects(tok):
        return [{"key": "demo-bot", "name": "Demo Bot"}]

    async def fake_classify(text, projects, key, **kw):
        return _draft(conf=0.9)

    async def fake_extract(text, key, **kw):
        return {"atoms": [], "recency": {"is_stale": False}}

    def boom(*a, **kw):
        raise AssertionError("run.py не имеет права писать в Мионику")

    deleted = []
    monkeypatch.setattr(run_mod.cp, "delete_job", lambda *a: deleted.append(a))
    monkeypatch.setattr(run_mod.file_backend.mio, "fetch_projects", fake_projects)
    monkeypatch.setattr(run_mod.file_backend.mio, "write_proposed", boom)
    monkeypatch.setattr(run_mod, "classify", fake_classify)
    monkeypatch.setattr(run_mod.cu, "extract_atoms", fake_extract)

    cfg = _cfg(tmp_path)   # skip_after_process=False по умолчанию — delete_job звать не должен
    store = Store(cfg.db_path)

    asyncio.run(run_mod.process_job(cfg, _Bot(), store, "j1"))

    assert store.is_key_processed("sha2") is True
    assert deleted == []                              # skip_after_process=False — job не трогаем
    assert store.get_queue_row("j1")["status"] == "awaiting"


def test_run_cycle_pings_when_queue_has_awaiting_rows(tmp_path, monkeypatch):
    """Пинг про очередь уходит из рабочего цикла (run_cycle), а не из process_job:
    непустая очередь без новых звонков — обычное состояние вечером и в выходные,
    и именно тогда пинг обязан сработать (см. run_cycle.__doc__)."""
    monkeypatch.setattr(run_mod.cp, "list_processable", lambda *a: [])   # тик ничего не находит

    cfg = _cfg(tmp_path)
    store = Store(cfg.db_path)
    store.upsert_queue_row("old1", status="awaiting")    # уже в очереди, до этого цикла

    bot = _Bot()
    asyncio.run(run_mod.run_cycle(cfg, bot, store))

    assert bot.sent and cfg.dashboard_url in bot.sent[0]
