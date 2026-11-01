-- Migration 002: add num_speakers hint column to jobs
-- Run once on the server: sqlite3 /path/to/db.sqlite < 002_num_speakers.sql
-- (or equivalent for your DB engine)
ALTER TABLE jobs ADD COLUMN num_speakers INTEGER;
