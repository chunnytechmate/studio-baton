# Changelog

Notable changes per release. Anything that changes what a studio has to do is
under **Upgrading**; the rest is grouped by what it affects.

## Unreleased

Four items from the studio's 2026-08 production-issue list. Exit codes are
unchanged throughout; two long-standing refusals were narrowed on purpose,
and both narrowings are named below.

### Upgrading

- `lesson clear --yes` no longer deletes drafts whose publish left work owed
  (a target that is not `ok`): the draft is the only record that the work is
  still owed, and sweeping it away is how an unfinished publish is forgotten.
  It now reports what it kept; pass `--force` to delete those too. Drafts
  with nothing owed on them clear as before.
- `calendar book` and `calendar schedule` accept a unique partial name, where
  they used to refuse everything but an exact match. If a studio relied on
  the strict refusal, nothing regresses — an ambiguous partial still exits 3
  with candidates — but a match the operator did not intend is now possible;
  the report names it in `matched` every time the relaxation fires, and
  `cancel` and the `lesson` commands keep the strict gate.

### Calendar

- The relaxation above: one substring match resolves and is announced; zero
  or several still stop and ask. In `schedule`, a learner named twice under
  two spellings ("Ada" and "Ada Whitfield") blocks the second slot instead of
  refusing the whole day, and the block names the slot to remove; a line that
  matches nobody is likewise a blocked slot, not an abandoned day.

### Lessons

- `lesson list` now carries each draft's full publish state per target —
  status, last error, attempts, and time — falling back to the published
  record where a re-staged draft is blind. A heartbeat asking "is this
  stuck?" no longer has to open the draft by hand.
- Vocabulary, in three layers that all warn rather than rewrite: a pool under
  `summary.vocabulary` rides the `lesson contract` payload with an
  instruction to use those spellings; `lesson stage --corrected/--corrected-file`
  keeps corrected notes beside the raw ones (the contract serves the
  corrected text; the raw notes are never overwritten); and `lesson ingest`
  reports near-miss spellings in `warnings` and on stderr without refusing
  the summary — a summary rejected over a spelling is a summary that stops
  being produced.

## 0.5.0

Four gaps closed from the 2026-08-28 port-gap audit against the studio's
legacy scripts. Everything here is additive: no existing command's output
shape or exit code changed, and every new behaviour is either a new
subcommand or config-gated off by default.

### Calendar

- Weekday names, time words (`โมง`/`นาฬิกา`/`ทุ่ม`/`ตี`/`เที่ยง`/`เที่ยงคืน`),
  and day-first dates join the grammar in `whenever.py` — vocabulary is
  configuration (`calendar.weekdays`, `calendar.time_words`), matching
  `date_shorthand`'s existing pattern. Day-first parsing is
  `calendar.accept_dmy`, off by default: day-first is a convention, not a
  universal, and `YYYY-MM-DD` is never ambiguous. Bare `N โมง` reads
  literally — `9 โมง` is 09:00, not the traditional Thai count — and a time
  past 23 hours is refused rather than wrapped.
- `calendar list --from/--to` shows a whole range, one entry per day, empty
  days included — a gap is information. The bare `calendar list <date>`
  form is unchanged.

### Learners

- `learner add` enrols a learner and, optionally, their session pages from
  Notion URLs (`--page-urls`/`--pages`) — the write path `learner
  list`/`show` never had. Refuses an exact-name duplicate outright (exit
  5); a near-miss is only ever reported alongside a success, never
  blocking one. `learner.instruments`/`learner.tones` restrict
  `--instrument`/`--tone` when the profile sets them (both empty, and
  unrestricted, by default). A studio-specific column named on the command
  line (`--prompt-level`, `--master-link`) with no `db.fields` entry to
  write it to is a configuration error raised before anything is written,
  not a silently dropped field.
- `learner list`'s human line now names the current piece by title, not
  only its id.

### Songs

- New command group `baton song list|search|show|add|update|remove` — the
  write path the piece catalogue never had. `remove` is refused while any
  learner is still assigned (exit 5, naming who), the same guard the
  legacy song manager used. `update` takes plain strings per field: a flag
  left out leaves it alone, an explicit empty value clears it — the same
  convention `Piece` already uses everywhere for "no link". Unknown ids on
  `update`/`remove` are refused rather than treated as a silent success.
- New skill `studio-songs`; `student-lookup` gains the `learner add` row.

### Store layer

- `LearnerStore` gains `add_learner`, `add_session`, `add_piece`,
  `update_piece`, `delete_piece`, implemented for SQLite, PostgREST/
  Supabase, and the fallback store — writes still never fail over, the
  fallback store's one rule. `FieldMap.extra_columns()` resolves
  studio-specific columns the model has no field for, and refuses an
  unmapped one before any write happens.

## 0.4.2

Everything here came out of running 0.4.1 through a real teaching day
(2026-08-27) and reading the summary it produced.

### Upgrading

**A summary now needs a `progress` section once the learner has a previous
session.** `lesson ingest` refuses without one — exit 4, pointing at
`/progress`. Each entry is the state `before` and the state `after`. Nothing
changes for a first lesson, which has nothing to compare against.

**Three wording rules now reject what they used to allow**, all in the same
layer that already refused emoji and links in a parent's message: a fact
restated in more than two sections, a rating where an observation belongs
("did very well"), a word about the child rather than the playing ("a weak
point"), and a practice goal nobody can do at home. Each comes back with a
pointer and a hint, so the fix is one round trip. The lists live under
`summary.body` and emptying one turns its rule off.

Both are steered by `lesson contract`, which hands the model the schema and the
rules fresh on every call — an agent that reads the contract adapts on its own.
An agent working from a cached prompt will hit exit 4 once and then adapt.

**`summary.sections` gains `progress`.** A studio that renamed its headings
should name that one too. Do **not** add `progress` to `prep.required`: every
page published before this release lacks the heading, and prep is fail-closed.

### Lesson summaries

- `progress` as a first-class section, rendered `before → after`.
- `summary.tones`, `summary.instruments`, and `summary.no_instrument_at_home`
  turn the `tone`, `instrument`, and `has_instrument` columns — all three
  present since the first migration, none of them read — into guidance for the
  learner being written about.
- A published record keeps the validated summary, not only the message sent to
  the family, so the next lesson is staged against what happened.
- `lesson publish` takes `--session N`, which refuses if the staged draft is
  for a different one. Every `lesson` subcommand takes `--learner NAME` as an
  alternative to the positional name.

### Recordings

- **The song a lesson works on is no longer sent as its recording.** A `video`
  block now wins over a bookmark or an embed wherever it sits, and callers pass
  the piece's `source_link` so the song is never mistaken for the lesson. A
  page holding only the song reads as having no recording, and the send gate
  refuses — which is the correct answer.
- `lesson publish` links a recording the pipeline uploaded but never put on the
  page, including on a re-run of an already-published lesson.

### Video pipeline

- New clips under a finished job start the next session by themselves;
  `video forget` is no longer part of the weekly loop.
- Source clips a completed job's record claims were trashed are re-trashed when
  the source still lists them.
- `doc_linked` is verified against the live page rather than its own record,
  and a document-store outage no longer turns a finished job into a failed one.
- `resume` sees what `run` sees, and says so when clips wait that only `run`
  may collect.
- `video status --json` reports "no error" as `null` rather than `""`.
- `job list` hides finished jobs older than `--stale-days` (default 3).

### Learners

- `learner assign --update-published` rewrites the piece section on sessions
  already published, matching on the snapshot's piece id rather than on the
  song's title. `--dry-run` shows the plan. Note that the replaced section is
  appended, so it moves to the end of the page.

## 0.4.1 and earlier

Not recorded here; see the git history. The audit of what the move from the
studio's previous scripts left behind lived in `docs/migration-audit.md` until
0.4.2 — its fourteen fixed findings are explained in the code they changed, and
its six open ones are issues #74-#79.
