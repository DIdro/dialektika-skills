import sqlite3
import json
from datetime import datetime


def _naive(dt: datetime) -> datetime:
    """Отбросить часовой пояс, чтобы даты можно было вычитать.

    В одной колонке соседствуют значения обоих видов: `decided_at` пишется
    приложением с зоной (`datetime.now(timezone.utc)`), а `recorded_at` — это
    голая дата записи звонка, без зоны; ручные правки через sqlite добавляют
    третий вид. Вычитание разнородных дат роняет весь эндпоинт метрик
    (так уже было: : `/metrics` отвечал 500 на каждый запрос).
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

# ── очередь приёмки (веб-ревью звонков) ──────────────────────────────────────
# _JSON_FIELDS: имя питоновского поля → имя колонки, где оно хранится сериализованным JSON.
_JSON_FIELDS = {"tasks": "tasks_json", "atoms": "atoms_json", "gate_flags": "gate_flags",
                "alternatives": "alternatives_json"}
_BOOL_FIELDS = {"diarized", "owner_identified"}

# все колонки строки очереди (без job_id — он PRIMARY KEY и передаётся отдельно)
QUEUE_COLUMNS = ("source", "summary", "count", "project", "confidence", "tasks_json",
                 "atoms_json", "recorded_at", "origin", "gate_flags", "diarized",
                 "speaker_count", "unknown_share", "owner_identified", "status",
                 "decided_at", "reparse_prompt", "context_done",
                 # чем ещё модель колебалась и что сама назвала непонятным —
                 # показывается в строке очереди рядом с выбранным проектом
                 "alternatives_json", "uncertainty")

# Колонки, которые ТОЛЬКО читаются. ts (когда строка попала в очередь) отдаётся
# витрине как запасная дата: у звонков без даты в имени файла recorded_at пуст,
# и колонка «Дата» в разборе оказывалась пустой — свежий звонок в очереди было
# не найти. Держим отдельно от QUEUE_COLUMNS: тот же кортеж работает белым
# списком на запись (upsert_queue_row), а ts проставляет сама база.
QUEUE_READ_COLUMNS = ("job_id",) + QUEUE_COLUMNS + ("ts",)


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as c:
            # WAL: демон разбора и API-процесс пишут в одну базу двумя процессами —
            # обычный journal_mode блокирует читателей на время записи, WAL — нет.
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(
                "CREATE TABLE IF NOT EXISTS processed ("
                "job_id TEXT PRIMARY KEY, transcript TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS reviews ("
                "job_id TEXT PRIMARY KEY, source TEXT, summary TEXT, count INTEGER, "
                "status TEXT DEFAULT 'awaiting', ts TEXT DEFAULT CURRENT_TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
            # идемпотентные миграции для старых БД
            for col, decl in (("project", "TEXT"),
                              ("context_done", "INTEGER DEFAULT 0"),
                              ("confidence", "REAL"),
                              ("tasks_json", "TEXT"),
                              ("recorded_at", "TEXT"),
                              ("atoms_json", "TEXT"),
                              ("gate_flags", "TEXT"),
                              ("origin", "TEXT"),
                              ("decided_at", "TEXT"),
                              ("reparse_prompt", "TEXT"),
                              ("diarized", "INTEGER"),
                              ("speaker_count", "INTEGER"),
                              ("unknown_share", "REAL"),
                              ("owner_identified", "INTEGER"),
                              ("alternatives_json", "TEXT"),
                              ("uncertainty", "TEXT")):
                try:
                    c.execute(f"ALTER TABLE reviews ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass
            # Одноразовая миграция строк, накопленных ПРЕЖНИМ потоком (разбор в
            # телеграм-боте). У них материал уже уехал в граф старым путём, а колонка
            # atoms_json не заполнена вовсе — новой её добавил ALTER выше. Приёмка
            # такой строки записала бы то же самое второй раз, поэтому уводим их в
            # отдельный статус legacy: из очереди ожидающих они пропадают, но не
            # исчезают молча — видны в режиме «все» с честной подписью.
            # Идемпотентна, как и соседние: после первого прохода таких строк нет
            # (статус уже legacy), а atoms_json IS NULL — это именно «не заполнена»,
            # пустой список хранится как '[]' и под условие не попадает.
            c.execute("UPDATE reviews SET status='legacy' "
                      "WHERE status='awaiting' AND atoms_json IS NULL")
            # таблица для отслеживания обработанных recording-key (дедуп)
            c.execute(
                "CREATE TABLE IF NOT EXISTS processed_keys ("
                "key TEXT PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP)")

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def is_processed(self, job_id: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM processed WHERE job_id=?", (job_id,)).fetchone()
            return row is not None

    def mark_processed(self, job_id: str, transcript_text: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO processed(job_id, transcript) VALUES (?,?)",
                      (job_id, transcript_text))

    def get_transcript(self, job_id: str):
        """Своя копия транскрипта звонка (нужна для записи контекста и для reparse в веб-очереди)."""
        with self._conn() as c:
            row = c.execute("SELECT transcript FROM processed WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else None

    def upsert_queue_row(self, job_id: str, **fields) -> None:
        """Создаёт или обновляет строку очереди. Пишет ТОЛЬКО переданные поля —
        частичное обновление не должно затирать соседей."""
        cols, vals = [], []
        for key, value in fields.items():
            col = _JSON_FIELDS.get(key, key)
            if col not in QUEUE_COLUMNS:
                raise ValueError(f"неизвестное поле очереди: {key}")
            if key in _JSON_FIELDS:
                value = json.dumps(value or [], ensure_ascii=False)
            elif key in _BOOL_FIELDS and value is not None:
                value = int(bool(value))
            cols.append(col)
            vals.append(value)
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO reviews(job_id, status) VALUES (?, 'awaiting')",
                      (job_id,))
            if cols:
                assign = ", ".join(f"{c_}=?" for c_ in cols)
                c.execute(f"UPDATE reviews SET {assign} WHERE job_id=?", (*vals, job_id))

    def _row_to_queue(self, row) -> dict:
        d = dict(zip(QUEUE_READ_COLUMNS, row))
        d["tasks"] = json.loads(d.pop("tasks_json") or "[]")
        d["atoms"] = json.loads(d.pop("atoms_json") or "[]")
        d["gate_flags"] = json.loads(d["gate_flags"] or "[]")
        d["alternatives"] = json.loads(d.pop("alternatives_json") or "[]")
        for b in _BOOL_FIELDS:
            d[b] = None if d[b] is None else bool(d[b])
        return d

    def get_queue_row(self, job_id: str):
        """Одна строка очереди приёмки по job_id (со всеми полями), либо None."""
        cols = ", ".join(QUEUE_READ_COLUMNS)
        with self._conn() as c:
            row = c.execute(f"SELECT {cols} FROM reviews WHERE job_id=?", (job_id,)).fetchone()
        return self._row_to_queue(row) if row else None

    def list_queue(self, status: str | None = None):
        """Строки очереди, новые сверху; можно отфильтровать по статусу."""
        cols = ", ".join(QUEUE_READ_COLUMNS)
        sql = f"SELECT {cols} FROM reviews"
        args = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        # ts вторым ключом: recorded_at — голый день, внутри дня иначе порядок вставки.
        sql += " ORDER BY COALESCE(recorded_at, ts) DESC, ts DESC"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row_to_queue(r) for r in rows]

    def queue_metrics(self, days: int = 7) -> dict:
        """Четыре метрики из спеки, все выводятся из самой очереди.

        reworked_share — доля решённых, которые владелец не принял: прямой сигнал
        качества классификатора. Считается от решённых, а не от всех, иначе
        свежая очередь занижает его до нуля.

        В решённые (и, соответственно, в reworked_share) НЕ входит статус split:
        разнесение звонка на несколько проектов — это расщепление строки на
        потомков, а не решение о качестве разбора (каждый потомок будет принят
        или отклонён отдельно). Если засчитать split как отклонение, метрика
        качества классификатора будет систематически завышена.
        """
        rows = [r for r in self.list_queue()
                if (r["decided_at"] or r["status"] == "awaiting")]
        decided = [r for r in rows if r["status"] in ("accepted", "rejected")]
        reworked = [r for r in decided if r["status"] == "rejected"]
        flags: dict[str, int] = {}
        for r in rows:
            for f in r["gate_flags"]:
                flags[f] = flags.get(f, 0) + 1
        deltas = []
        for r in decided:
            if r["decided_at"] and r["recorded_at"]:
                try:
                    d = (_naive(datetime.fromisoformat(r["decided_at"]))
                         - _naive(datetime.fromisoformat(r["recorded_at"]))).total_seconds() / 60
                    deltas.append(d)
                except (ValueError, TypeError):
                    pass
        deltas.sort()
        median = deltas[len(deltas) // 2] if deltas else None
        return {"median_minutes_to_decision": median,
                "reworked_share": (len(reworked) / len(decided)) if decided else 0.0,
                "flag_counts": flags,
                "awaiting": sum(1 for r in rows if r["status"] == "awaiting")}

    def get_kv(self, k: str):
        """Обобщённое key-value чтение/запись — этой же таблицей kv пользуется
        notifier.ping_pending для кулдауна (ключ last_ping_ts)."""
        with self._conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def set_kv(self, k: str, v: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, v))

    def is_key_processed(self, key: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM processed_keys WHERE key=?", (key,)).fetchone()
            return row is not None

    def mark_key_processed(self, key: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO processed_keys(key) VALUES (?)", (key,))
