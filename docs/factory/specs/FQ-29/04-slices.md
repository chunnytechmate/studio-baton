# Gate 4 — Vertical slices

Each implementation PR stays under 400 changed lines, starts from the preceding
merged slice, adds its own failing test proof, and is independently usable.

## Slice 0 — freeze the lesson's Song DB context (tracer bullet)

Mostly mocked boundary: fake learner store and CLI report, with no document or
message write.

`done_when`: staging a fictional learner on song A persists a typed snapshot;
changing the fake live assignment to B before contract generation still exposes
A and no live `current_piece_id`; explicit no-song and legacy-unavailable remain
distinct; malformed snapshots and dangling song ids fail closed before save.

Expected files: `src/baton/pipelines/staging.py`,
`src/baton/cli/cmd_lesson.py`, `tests/test_piece_snapshot.py`.

Gate `full`; load-bearing `false`; confidence `high`. This proves the complete
stage-to-summarizer invariant while document and send consumers remain mocked.

## Slice 1 — publish frozen Song DB resources safely

Replace the mocked document boundary with `FakeDocStore` exercising the real
renderer, publisher, preservation policy, and published-record handoff.

`done_when`: a captured snapshot renders the exact title/source/practice/sheet
order before summary, omits blank links, gives dry-run parity, persists unchanged,
deduplicates only exact preserved resources for the same snapshot, and refuses a
changed or legacy-unknown forced republish before any document mutation; existing
summary-only publisher callers remain compatible.

Expected files: `src/baton/render/piece.py`,
`src/baton/pipelines/publish.py`, `src/baton/pipelines/staging.py`,
`src/baton/cli/cmd_lesson.py`, `tests/test_piece_publish.py`.

Gate `full`; load-bearing `false`; confidence `medium` because preserved blocks
have identity but no persisted ownership ids.

## Slice 2 — send only the published practice track and document behavior

Replace the mocked post-publish consumer with the real gather/gate/compose path
and `FakeMessenger`; this is the only slice changing an existing test.

`done_when`: after publishing song A and assigning live song B, contract,
document, published record, and message still use A; legacy/unavailable records
never fall back to B and the configured gate warns or blocks; current instrument
still resolves normally; README and Notion setup explain snapshot timing, block
shapes, preserve behavior, and the forced-republish remedy.

Expected files: `src/baton/pipelines/send.py`, `tests/test_send.py` (only the
owner-approved fixture/live-song assertion), `tests/test_lesson_piece_flow.py`,
`README.md`, `docs/notion-setup.md`.

Gate `deep`; load-bearing `true`; confidence `medium`. Keep the PR Draft for a
human read and install `pip-audit` only in local `.venv`, without manifest edits.

## Queue order

Slice 0 -> Slice 1 -> Slice 2. Only Slice 0 is initially unblocked; later issues
use `factory:wait-to-implement` until their predecessor merges, preventing two
branches from inventing incompatible snapshot shapes.
