-- Studio Baton — SQLite schema.
--
-- Matches the default table and column names in src/baton/defaults.yaml. If
-- you already have a database, do not run this: point db.tables and db.fields
-- at your own names instead, then run `baton doctor` to confirm the mapping.
--
--   sqlite3 data/studio.db < migrations/sqlite.sql

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS learners (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    instrument       TEXT    NOT NULL DEFAULT '',
    -- Free text: 'standard', 'child', 'exam', 'casual', or a word of your
    -- own. `lesson contract` hands the model the wording guidance filed
    -- under this word in `summary.tones`; a word with no entry there
    -- changes nothing, so name it in both places or in neither.
    tone             TEXT    NOT NULL DEFAULT 'standard',
    -- Whether they have an instrument at home. False adds
    -- `summary.no_instrument_at_home` to the summariser's instructions, so
    -- the practice goals come back as listening, counting, and reading —
    -- goals are never dropped, since a lesson always leaves something to do.
    has_instrument   INTEGER NOT NULL DEFAULT 0,
    current_piece_id INTEGER REFERENCES pieces(id) ON DELETE SET NULL,
    -- Whether they still study here. Deactivating removes a learner from
    -- matching and rosters; nothing is ever deleted, and `learner activate`
    -- brings them back.
    is_active        INTEGER NOT NULL DEFAULT 1,
    -- Fully out of the way: hidden even from `learner list --all`, unlike
    -- is_active. NULL means not trashed. `learner trash`/`learner untrash`
    -- are the only things that ever touch this. Nothing is ever deleted.
    deleted_at       TEXT    DEFAULT NULL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pieces (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL,
    source_link    TEXT    NOT NULL DEFAULT '',
    practice_track TEXT    NOT NULL DEFAULT '',
    sheet_link     TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per numbered session. `doc_id` points at the page a learner reads;
-- the session's status and date live on that page, never duplicated here —
-- two copies of a status is how the calendar and the documents drifted apart
-- in the system this replaces.
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
    number     INTEGER NOT NULL,
    doc_id     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (learner_id, number)
);

CREATE TABLE IF NOT EXISTS works (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id     INTEGER NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    type           TEXT    NOT NULL DEFAULT 'performance',
    video_link     TEXT    NOT NULL DEFAULT '',
    -- Optional second home of the recording (a Drive file beside a YouTube
    -- upload). Sending a work means sending whichever links exist.
    drive_link     TEXT    NOT NULL DEFAULT '',
    performed_date TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per recurring weekly hour: the day a learner comes and the time
-- their lesson starts. The words in `weekday` are the same seven English day
-- names the Notion dashboard tags use, so the two never need translating.
-- The calendar holds the booked lessons; this table holds the standing
-- weekly pattern behind them.
CREATE TABLE IF NOT EXISTS lesson_slots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
    weekday    TEXT    NOT NULL,
    start_time TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (learner_id, weekday, start_time)
);

CREATE INDEX IF NOT EXISTS idx_sessions_learner ON sessions (learner_id, number);
CREATE INDEX IF NOT EXISTS idx_works_learner    ON works (learner_id, performed_date DESC);
CREATE INDEX IF NOT EXISTS idx_lesson_slots_learner ON lesson_slots (learner_id, weekday, start_time);
CREATE INDEX IF NOT EXISTS idx_learners_name    ON learners (name);
