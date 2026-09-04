# Changelog

Notable changes per release. Anything that changes what a studio has to do is
under **Upgrading**; the rest is grouped by what it affects. Every release back
to 0.1.0 has an entry, and every tag carries a GitHub release.

## 1.1.0 (2026-09-04)

The video pipeline decodes and re-encodes every clip it combines. On the
studio's own hardware that is 30 minutes of CPU per lesson and, on a burstable
cloud instance, more than the encode watchdog allows: a real five-minute
lesson filmed as one burst on one phone could no longer finish. Most lessons
are exactly that, and joining files that agree needs no compute at all.

### Video

- **Matching clips are now joined by stream copy.** Before encoding anything,
  the probed clips are compared on everything the concat demuxer is strict
  about and the concat filter never was: codec, profile, pixel format, frame
  rate, time base, frame size (rotation already folded in), sample aspect
  ratio, HDR state, and the audio codec, rate, and layout. Full agreement
  joins the files at packet boundaries with `-c copy`: nothing is decoded, so
  nothing is re-encoded, and the deliverable is first-generation rather than
  a second lossy pass over footage the phone already compressed. On the clips
  that motivated this, the join is I/O-bound.
- **The copy is verified, then trusted.** The joined file's duration is
  checked against the sum of its parts; a copy that lands on the wrong length
  (edit lists, mismatched time bases) is discarded and the normalising encode
  runs instead. Every failure of the copy path falls back rather than fails:
  unknown probe, disagreement, ffmpeg error, timeout.
- **The job record says which path ran.** `combined.method` is `stream-copy`
  or `encode`, so `baton video status` shows which sessions skipped the
  encode. `VideoEncoder.combine` now returns a `CombineResult` carrying the
  path and the method instead of a bare `Path`; the atomic-write promise is
  unchanged.
- **A single-clip session is a remux.** One clip has nothing to disagree
  with, so it now copies too, where it was previously re-encoded on its own.
- **What still encodes:** any disagreement between clips; a forced
  `media.encode.fps`; HDR sources under an enabled tone-map; the `1080p`
  profile over any other size. Each of these asks for a change a copy cannot
  make. `media.encode.copy_when_safe: false` restores always-encode.

## 1.0.5 (2026-09-02)

One fix, found by running 1.0.4 against the real calendar an hour after
deploying it.

### Sending

- `send readiness` and `send aftermath` now read past the icon a calendar
  title starts with. Both built their scheduler without the profile's
  `calendar.event_emoji`, leaving the title match only the bare `Name (`
  prefix to try. `baton calendar` writes every title as `🎸 Name (Week N)`,
  so on a studio that uses icons the roster came back empty on a full
  teaching day and every real booking was listed as naming nobody.
  `baton calendar` had passed the icons all along; the reports had not, since
  they shipped in 0.7.0.
- The roster test now books an icon-prefixed title. The old one used bare
  names, which is how this survived four releases.

## 1.0.4 (2026-09-02)

A writing pass over everything a person reads: the messages parents receive,
the reports the teacher reads, and the prose in the code and docs. No
behaviour changed, but wording did, so anything asserting on Baton's exact
output needs a look.

### Upgrading

- Message and report wording changed in several places. Nothing parses
  Baton's human output (that is what `--json` is for), but a studio scripting
  around the exact strings should read the list below.

### Sending

- The YouTube description no longer apologises in advance for the summary
  being wrong. The teacher reads every summary before it goes out, so the
  apology only spent trust it had no reason to spend. The signature stays.
- The lesson message links the session page as `Week N` instead of
  `ลิ้งค์ Link Week N`, which said "link" twice and misspelled it.
- The recording message says `รายละเอียดการเรียน` where it used to say
  `รายละเอียด Notion`. A parent opens a lesson page, not a vendor.
- `send readiness` and `send aftermath` call a calendar entry
  `รายการในปฏิทิน` rather than `คิว`, and both now say `หลักฐาน` for a
  delivery receipt instead of one saying `ใบเสร็จ`.

### Summaries

- A covered topic renders as `topic: detail` on the page, in Markdown, and in
  the YouTube description. The separator used to be an em dash.

### Naming

- The upload title and the calendar event description separate a learner from
  their session with a hyphen instead of an em dash.
- `วิดีโอ` is now the only spelling; the messages used to write `วีดีโอ`
  while the docs wrote `วิดีโอ`.
- `baton doctor` reports `พบ driver ของ {kind}`, matching the `driver` key
  the profile actually sets, instead of transliterating it.

### Docs

- README.th.md drops `จองคาบ` for `นัดหมาย`, a word the reader outside this
  studio would stumble on, and replaces the "four stages" and "one lesson's
  cycle" framing with plain descriptions.
- U+2014 is gone from authored prose across the project, replaced per sentence
  by a period, comma, colon, or parentheses. It survives in exactly one place,
  commented at both sites: the free-slot marker a teacher types into a
  schedule, which is input rather than prose.

## 1.0.3 (2026-09-01)

Closes the last way an agent could send a lesson with no recording without
anyone having agreed to it.

### Upgrading

- `send lesson --without-video` now takes a value: the confirmation code
  `send video-waiver` texts to a configured contact for that exact learner
  and session. A bare `--without-video` no longer parses, and no value works
  unless a code was requested and is still live. Anything scripted around the
  old flag needs the two-step flow instead.
- `send batch` no longer accepts `--without-video` at all. One code answers
  one learner's one session, and a batch has many of both, so there was never
  a single value that could stand for all of them. A learner with no recording
  is reported blocked like any other refusal and sent on their own afterwards.

### Sending

- Added `send video-waiver NAME --to CONTACT`. It sends a one-time code
  through the studio's own messenger and never returns that code to the
  caller: not in `--json`, not in the human line, nowhere. Reaching it means
  reading the message it was sent in, which is what makes the confirmation a
  person's rather than something any caller can assert.
- Codes are scoped to one learner and session, single-use, and expire on
  their own (`summary.video_waiver.ttl_minutes`, default 30), so a code seen
  once is not a standing key.

## 1.0.2 (2026-09-01)

Three fixes from running the video pipeline against a real course a second
time, after a summary had already been published, and one change to how the
no-recording stop presents its way past.

### Upgrading

- `send`'s no-recording stop no longer returns `candidates` naming
  `--without-video`. The flag itself is unchanged; only the machine-readable
  contract lost the option that skipped the person it exists to consult. A
  caller that inspected `candidates[0].option` for that flow should read the
  new `details.missing` field instead, and `--without-video` still has to be
  typed by whoever decides.

### Video pipeline

- A recording now lands on the correct lesson whether it finishes before or
  after the summary does. `baton video` used to ask for the session in
  progress, then fall back to the next *empty* one; publishing a summary
  marks a session Done, which used to send a later recording onto next
  week's untaught page instead of the lesson it was filmed for.
- The recording is inserted above the summary regardless of which one was
  written first, instead of wherever an append happened to land. Notion has
  no operation to move a block that already exists, so this is spelled as an
  insert at the top of the page (`DocStore.append_blocks(..., position=)`).

### Summaries

- A tone can now rename the `goals` section and its message label, the same
  way a learner with no instrument at home already could
  (`summary.tone_overrides`). A studio whose tone means "no homework" no
  longer needs an unrelated database column to say so.

## 1.0.1 (2026-09-01)

This patch makes the public package describe the production system as clearly
as the CLI operates it. It changes no command, schema, exit code, or adapter.

### Documentation

- Added a complete Thai README and linked it from the package description.
- Reworked the English overview around the five handoffs of one lesson and
  corrected the inventory to 14 top-level groups and 63 user-facing command
  paths; the hidden `job supervise` path is not presented as a user command.
- Replaced stale pre-1.0 package metadata with the current Production/Stable
  status and the four-stage Class Summarize → PLAM → OpenClaw → Baton lineage.

### Thai locale

- Rephrased configuration, driver, model, and background-job diagnostics so
  they read as direct Thai instructions while preserving their keys and
  operational meaning.

## 1.0.0

The version the project was working towards: the whole cycle (booking,
video, summaries, delivery) has now run end to end on real teaching days, so
the package declares itself production/stable instead of alpha.

### Upgrading

- `baton send lesson` no longer refuses outright when a session has no
  recording link: it stops with exit 3 and asks a person, and
  `--without-video`: that person's confirmed answer: sends the message with
  no video section. An agent or script that treated exit 5 as the only
  outcome for a missing `video_link` should now expect exit 3 carrying
  `candidates`. Every other required field keeps the hard, unoverridable
  block, and a session that does have a recording keeps it, flag or no flag.
- `media.encode.fps` is now safe to set on every session shape. On 0.7.0 and
  earlier it broke the encode of a single-clip session (below), so studios
  hitting "Too many packets buffered for output stream 0:0" on VFR iPhone
  clips had to leave it unset; they may now set `media.encode.fps: 60`.

### Video

- A session with one clip no longer fails its encode when a video filter
  chain is in play: the lone clip's audio is mapped as a stream specifier
  (`0:a`), not as a filter-complex label, which is what `media.encode.fps`
  used to trip over.

### Sends

- A lesson that was never filmed is a decision, not a data gap. `send
  lesson` and `send batch` stop on exit 3 carrying the two real choices
  (send now with no video section, or put the recording on the document
  first); `--without-video` delivers the confirmed answer, applied the same
  way a studio relaxes its own gate in config, so the result still warns
  about the gap it sent without.

### Packaging

- The trove classifier moves from `Development Status :: 3 - Alpha` to
  `:: 5 - Production/Stable`.

## 0.7.0

The 2026-08-29 logic audit, in the recovery direction: what to do when
something has already gone out wrong, and the per-learner differences the
database had been carrying with nothing reading them.

### Upgrading

- A learner with `has_instrument` false now gets the `goals` section renamed
  on the page and in the parent's message: `summary.no_instrument.section`
  and `.message_label`. **A profile that publishes in a language other than
  the package default must set both**, or the section keeps its usual
  wording; nothing substitutes English into a translated page. The older
  `summary.no_instrument_at_home` still supplies the model instruction.
- `summary.body.goals_not_practicable` is now applied only to learners who
  have an instrument at home, and the phrases that ask for an attitude rather
  than an action move to the new `summary.body.goals_attitude`, which applies
  to everyone. A studio that customised the first list should split it the
  same way; leaving it alone means those phrases stop being refused for
  learners with nothing at home to practise on, which is the intended change.
- `DocStore.get_status` takes `with_blocks` (default `True`, so existing
  implementations keep working). Declining returns `block_count=None` rather
  than `0`: zero decides whether a summary may be written onto a not-started
  page, so an uncounted page must not report it.
- `media.youtube.max_parallel_uploads` is removed; nothing read it.
- `baton doctor` now fails a profile whose `llm.provider` names anything but
  `none`. Baton has no model client, and a profile expecting one is waiting
  for a call that never comes.

### Lessons

- `baton lesson unpublish` takes a published summary back off its page: the
  blocks the publish recorded, then the session back to in progress, the
  draft rewound to `summarised`, and the record removed so the lesson can be
  published again. A recorded block whose text was edited by hand stops the
  command (exit `3`) rather than being deleted anyway; a block no record
  names is kept. Records written before block ids existed are attributed by
  re-rendering the stored summary, and anything unaccounted for is reported
  as ambiguous instead of guessed at. `--whole-page --force` is the explicit
  recovery for a page that went to the wrong recipient. Nothing un-sends a
  message that already went out, and the report says so.
- `baton lesson stage-set --field titles|context|corrected_context` amends a
  staged lesson without re-staging and losing what `stage` gathered. A
  published draft refuses the amendment.
- A publish now records the ids of the blocks it appended, which is what lets
  `unpublish` name exactly what it owns.
- Per-learner voice, completed: a studio's own prompt level reaches the model
  through `summary.prompt_levels` (mapped by `db.fields.learner.prompt_level`),
  and a level the profile does not describe adds nothing rather than being
  guessed at: the same stance `tone` and `instrument` already took.

### Sending

- `baton send readiness --date DATE` lists who is booked that day and what
  would still block each message, recomputed through the same `evaluate` the
  send refuses through, so the report and the refusal cannot drift. It keeps
  the layers apart: not yet published, no summary, and no video block are
  three different fixes in a fixed order.
- `baton send aftermath --date DATE` reports what the day left behind:
  drafts that never reached publish, draft files whose learner no longer
  exists, and published lessons with no send receipt. The receipt check
  shares one key with `send lesson`, so a receipt written by one is found by
  the other, and a miss is reported as the absence of evidence inside the
  duplicate window rather than as proof nothing was sent.
- Both reports exit `0` whatever they find, and both name whether the day's
  roster came from the calendar or from the dates on the documents.

### Correctness

- `set_current_piece` no longer succeeds silently when it matched no row:
  SQLite checks the row count and PostgREST asks for `return=representation`,
  both raising `StateError`. `learner assign` printed "is now working on"
  straight after a write that had done nothing.
- The fake document store now tolerates deleting a block that is already
  gone, as the real one always did: a resume path that production handles
  was failing only in tests.
- A read served by `db.fallback` says so on stderr. The flag recording it had
  been set on every failover and read by nothing.
- `get_status` no longer lists a whole page to count blocks for the six
  callers that only wanted a status word or a date.

## 0.6.0

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
  the strict refusal, nothing regresses (an ambiguous partial still exits 3
  with candidates), but a match the operator did not intend is now possible;
  the report names it in `matched` every time the relaxation fires, and
  `cancel` and the `lesson` commands keep the strict gate.

### Calendar

- The relaxation above: one substring match resolves and is announced; zero
  or several still stop and ask. In `schedule`, a learner named twice under
  two spellings ("Ada" and "Ada Whitfield") blocks the second slot instead of
  refusing the whole day, and the block names the slot to remove; a line that
  matches nobody is likewise a blocked slot, not an abandoned day.

### Lessons

- `lesson list` now carries each draft's full publish state per target (
  status, last error, attempts, and time) falling back to the published
  record where a re-staged draft is blind. A heartbeat asking "is this
  stuck?" no longer has to open the draft by hand.
- Vocabulary, in three layers that all warn rather than rewrite: a pool under
  `summary.vocabulary` rides the `lesson contract` payload with an
  instruction to use those spellings; `lesson stage --corrected/--corrected-file`
  keeps corrected notes beside the raw ones (the contract serves the
  corrected text; the raw notes are never overwritten); and `lesson ingest`
  reports near-miss spellings in `warnings` and on stderr without refusing
  the summary: a summary rejected over a spelling is a summary that stops
  being produced.

## 0.5.0

Four gaps closed from the 2026-08-28 port-gap audit against the studio's
legacy scripts. Everything here is additive: no existing command's output
shape or exit code changed, and every new behaviour is either a new
subcommand or config-gated off by default.

### Calendar

- Weekday names, time words (`โมง`/`นาฬิกา`/`ทุ่ม`/`ตี`/`เที่ยง`/`เที่ยงคืน`),
  and day-first dates join the grammar in `whenever.py`: vocabulary is
  configuration (`calendar.weekdays`, `calendar.time_words`), matching
  `date_shorthand`'s existing pattern. Day-first parsing is
  `calendar.accept_dmy`, off by default: day-first is a convention, not a
  universal, and `YYYY-MM-DD` is never ambiguous. Bare `N โมง` reads
  literally: `9 โมง` is 09:00, not the traditional Thai count, and a time
  past 23 hours is refused rather than wrapped.
- `calendar list --from/--to` shows a whole range, one entry per day, empty
  days included: a gap is information. The bare `calendar list <date>`
  form is unchanged.

### Learners

- `learner add` enrols a learner and, optionally, their session pages from
  Notion URLs (`--page-urls`/`--pages`): the write path `learner
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

- New command group `baton song list|search|show|add|update|remove`: the
  write path the piece catalogue never had. `remove` is refused while any
  learner is still assigned (exit 5, naming who), the same guard the
  legacy song manager used. `update` takes plain strings per field: a flag
  left out leaves it alone, an explicit empty value clears it: the same
  convention `Piece` already uses everywhere for "no link". Unknown ids on
  `update`/`remove` are refused rather than treated as a silent success.
- New skill `studio-songs`; `student-lookup` gains the `learner add` row.

### Store layer

- `LearnerStore` gains `add_learner`, `add_session`, `add_piece`,
  `update_piece`, `delete_piece`, implemented for SQLite, PostgREST/
  Supabase, and the fallback store: writes still never fail over, the
  fallback store's one rule. `FieldMap.extra_columns()` resolves
  studio-specific columns the model has no field for, and refuses an
  unmapped one before any write happens.

## 0.4.2

Everything here came out of running 0.4.1 through a real teaching day
(2026-08-27) and reading the summary it produced.

### Upgrading

**A summary now needs a `progress` section once the learner has a previous
session.** `lesson ingest` refuses without one: exit 4, pointing at
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
rules fresh on every call: an agent that reads the contract adapts on its own.
An agent working from a cached prompt will hit exit 4 once and then adapt.

**`summary.sections` gains `progress`.** A studio that renamed its headings
should name that one too. Do **not** add `progress` to `prep.required`: every
page published before this release lacks the heading, and prep is fail-closed.

### Lesson summaries

- `progress` as a first-class section, rendered `before → after`.
- `summary.tones`, `summary.instruments`, and `summary.no_instrument_at_home`
  turn the `tone`, `instrument`, and `has_instrument` columns (all three
  present since the first migration, none of them read) into guidance for the
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
  refuses, which is the correct answer.
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

## 0.4.1 (2026-08-27)

Carries the 308 fix: a resumable YouTube upload's HTTP 308 is a success
shape, not a redirect to follow. The release itself taught two things the
repo kept: `pyproject.toml`'s version is static, so it must be bumped before
the tag is minted or PyPI refuses the wheel as the previous version; and the
documented pytest command must collect the whole suite.

## 0.4.0 (2026-08-27)

- Two publishes inside one second now order by session number, not by
  whichever the filesystem's glob yielded first: `send lesson` with no
  `--session` asks for the latest record, and the arbitrary winner could
  send last week's message to this week's family.
- Harness-compatibility gaps HR1–HR8 closed.

## 0.3.5 (2026-08-27)

- `send video`, `learner add-work`, and send readiness/aftermath's ancestor
  (findings F13, F17, F20 from the migration audit).
- `send` recognises bookmarked recordings and links the lesson page (F16,
  F18); `lesson publish` retries the YouTube description it owes (F5); F6,
  F7, F10, and F15 closed.

## 0.3.1 (2026-08-27)

- The practice track belongs to the lesson, not to today (#29): the piece is
  snapshotted when the lesson is staged, so a summary published Monday and
  sent Friday carries Monday's track.
- Mixed-orientation encodes, calendar retries, and the AI footer.

## 0.3.0 (2026-08-27)

The migration-audit release: the audit of what the move from the studio's
previous scripts left behind lived in `docs/migration-audit.md` until 0.4.2:
its fourteen fixed findings are explained in the code they changed, and its
six open ones are issues #74-#79. This release landed the audit lanes for
prep, notes, LINE send, video, calendar, lesson, and retired skills.

## 0.2.7 (2026-08-23)

A GPU encoder option: `media.encode.codec: h264_nvenc` moves the final
encode to NVIDIA NVENC. Decode and the concat filter stay on CPU: clips
come from phones and rarely share a codec or resolution.

## 0.2.6 (2026-08-23)

`baton lesson publish` updates the YouTube description, refusing to touch a
video the configured account does not own (a real incident, 2026-08-09: a
page's YouTube link pointed at an unrelated channel's tutorial).

## 0.2.5 (2026-08-23)

Three fixes found chasing a real report of a learner's video missing from a
`baton video` run: `_slug()` collapsed any non-ASCII (Thai) name to
"unknown", so such learners' job records aliased onto one file and a second
run silently inherited the first's completed state; two Drive clips sharing
a filename clobbered each other on disk before combining; and the lesson
message was recomposed to match the studio's existing LINE format after a
side-by-side comparison showed parents would notice the difference.

## 0.2.4 (2026-08-23)

Google credentials no longer rewrite the scopes of an authorized-user
refresh token: replacing them can make Google reject a valid token with
`invalid_scope` before any API call is attempted.

## 0.2.3 (2026-08-22)

- Google-backed media services read their credentials from one place:
  `media.google.credentials_file`, or the shared `*_env` trio, so Drive and
  YouTube no longer have to be configured separately.
- An argparse parse failure is raised as a typed error so the CLI keeps its
  exit-code and JSON envelope contract on a bad invocation, instead of
  argparse's own exit path; vendor exceptions from Google calls get the same
  treatment.

## 0.2.2 (2026-08-22)

- A refusal still reports `ok: false` with its JSON envelope; publishing
  finishes the session it wrote; a fence tag Notion does not know no longer
  costs the page.
- Docs: `learner latest` returns the page as sections, so "what did we do
  last time" needs no second command.

## 0.2.1 (2026-08-22)

`baton prep` (the day's lesson briefing, behind a hard gate) and `baton
course` (archive a finished course before emptying it: clear refuses an
unarchived course). Docs explain Baton's lineage and working cycle; the
call-graph extractor the published diagram is drawn from ships.

## 0.1.0 (2026-08-18)

First public release: learners, lesson summaries, messaging, video, and
calendar behind one fail-closed CLI. The release run caught that a
mis-scoped `include` in `pyproject.toml` produced a wheel of data files with
no Python in it, nothing was published, and the wheel check that caught it
now runs on every release.
