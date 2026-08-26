-- Invented data for the example profile, so a new install has something to
-- look at before it has anything real. Every name here is fictional.
--
--   sqlite3 profiles/example/data/studio.db < migrations/sqlite.sql
--   sqlite3 profiles/example/data/studio.db < migrations/seed_example.sql

-- ON CONFLICT DO NOTHING throughout: re-running this script against a
-- database that already has the sample rows (e.g. `init --force
-- --sample-data` a second time) must not raise sqlite3.IntegrityError on
-- the repeat primary-key / unique-column collision. `works` has no natural
-- key of its own, so it is given explicit ids here, matching the pattern
-- already used for `pieces` and `learners`, purely so a repeat run has
-- something to conflict on.

INSERT INTO pieces (id, title, source_link, practice_track, sheet_link) VALUES
    (1, 'Autumn Leaves',        'https://example.invalid/autumn-leaves', 'https://example.invalid/tracks/autumn.mp3', 'https://example.invalid/sheets/autumn.pdf'),
    (2, 'Blackbird',            'https://example.invalid/blackbird',     '',                                          'https://example.invalid/sheets/blackbird.pdf'),
    (3, 'Minuet in G',          '',                                      'https://example.invalid/tracks/minuet.mp3', ''),
    (4, 'Take Five',            'https://example.invalid/take-five',     '',                                          '')
ON CONFLICT DO NOTHING;

INSERT INTO learners (id, name, instrument, tone, has_instrument, current_piece_id) VALUES
    (1, 'Ada Whitfield',  'guitar', 'standard', 1, 2),
    (2, 'Bruno Castell',  'drums',  'casual',   0, 4),
    (3, 'Clara Nguyen',   'piano',  'exam',     1, 3),
    (4, 'Devon Marsh',    'guitar', 'child',    1, NULL)
ON CONFLICT DO NOTHING;

INSERT INTO sessions (learner_id, number, doc_id) VALUES
    (1, 1, 'doc-ada-01'),
    (1, 2, 'doc-ada-02'),
    (1, 3, 'doc-ada-03'),
    (2, 1, 'doc-bruno-01'),
    (2, 2, 'doc-bruno-02'),
    (3, 1, 'doc-clara-01'),
    (4, 1, 'doc-devon-01')
ON CONFLICT DO NOTHING;

INSERT INTO works (id, learner_id, title, type, video_link, performed_date) VALUES
    (1, 1, 'Blackbird',      'recital', 'https://example.invalid/watch/ada-blackbird', '2026-06-14'),
    (2, 1, 'Autumn Leaves',  'cover',   'https://example.invalid/watch/ada-autumn',    '2026-04-02'),
    (3, 3, 'Minuet in G',    'exam',    '',                                            '2026-07-30')
ON CONFLICT DO NOTHING;
