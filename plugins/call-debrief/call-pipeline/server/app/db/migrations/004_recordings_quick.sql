-- Fast Groq transcript for `pipeline record --quick`. Applied idempotently on
-- startup by db.session._ensure_columns(); this file is the canonical record.

ALTER TABLE recordings ADD COLUMN quick_transcript TEXT;
