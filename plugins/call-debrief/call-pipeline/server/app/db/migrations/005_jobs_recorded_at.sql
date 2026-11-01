-- Дата записи звонка (recorded_at), извлекается из имени файла при upload (или явный параметр).
-- Применяется идемпотентно на старте db.session._ensure_columns(); этот файл — канонический record.

ALTER TABLE jobs ADD COLUMN recorded_at TEXT;
