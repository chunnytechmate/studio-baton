# Migration audit: OpenClaw skills → Studio Baton

Studio Baton replaced a set of hand-written OpenClaw skills one domain at a
time. Each rewrite was a chance to drop a behaviour that the old script had
learned the hard way and that nothing in the new code records the absence of.
One such gap reached production: `baton video` fed phone clips straight into
ffmpeg's `concat` filter without the per-clip normalisation the old
`video_tools.py` did in a separate pass, so any lesson filmed partly in
landscape and partly in portrait failed to encode. It had been that way since
the rewrite; it surfaced only when a learner happened to film that way.

This document is the standing audit that looks for the rest of them. It is a
working file: claim a lane before auditing it, record what you checked, and
leave the verdict where the next person can find it.

**Started:** 2026-08-27 · **Baton version at start:** 0.2.7 · **Fixes shipped in:** 0.3.1 (F1–F4, via #70), 0.3.2 (F6, F7, F10, F15), 0.3.3 (F5, F16, F18), 0.3.5 (F13, F17, F20)

---

## How to audit a lane

1. **Claim it** — put your name and the date in the Owner column below, commit,
   and only then start. Two people auditing the same lane is the waste this
   file exists to prevent.
2. **Read the old skill first, not the new code.** The question is "what did
   this do", not "does the new code look reasonable". A capability nobody
   remembers is exactly the one that gets dropped.
   Old skills live in `~/.openclaw/workspace/skills/<name>/` (and
   `archived-skills/` for retired ones). Read `SKILL.md`, then every script —
   including the `notes/`, `KNOWN_ISSUES.md`, and `TROUBLESHOOT*.md` files,
   which is where the hard-won behaviours are written down.
3. **List capabilities, then check each one against Baton.** Not just the happy
   path: retries, gates, orderings, fallbacks, error text, idempotency,
   cleanup, what happens on partial failure.
4. **Prove it, don't infer it.** Reproduce the old behaviour and the new one.
   For anything ffmpeg- or API-shaped, run it in the `openclaw-gateway`
   container, not on the host — versions differ and so does the wording of
   errors.
5. **Record the verdict below** — including "checked, no gap", which is worth
   as much as a finding and stops the lane being re-audited.

### What counts as a finding

A behaviour the old skill had, that a studio depends on, that Baton does not
have. Three flavours, all worth reporting:

- **Dropped capability** — the old code did it, the new code does not.
- **Silent degradation** — the new code appears to do it but does not (a
  config value with no branch behind it, a docstring that promises more than
  the chain delivers).
- **Lost hard-won detail** — a retry, an ordering, an error message, or a gate
  that exists because of a specific incident, and whose absence only shows up
  during the next one.

Not a finding: Baton doing something differently but no worse, or a
deliberately-deferred decision that is written down as deferred.

---

## Lanes

| # | Domain | Old skill | Baton counterpart | Status | Owner |
|---|--------|-----------|-------------------|--------|-------|
| 1 | Video encode | `video_pipeline/core/video_tools.py` | `adapters/media/ffmpeg.py` | ✅ done — 1 finding, fixed | Claude 2026-08-27 |
| 2 | Video: Drive + YouTube + orchestration | `video_pipeline/core/{drive_manager,drive_cleanup,youtube_uploader,pipeline_orchestrator}.py` | `adapters/media/google.py`, `pipelines/video.py` | 🟡 partial — spot-checked, see notes | Claude 2026-08-27 |
| 3 | Calendar | `google-calendar/scripts/{scal,gcal}.py` | `pipelines/schedule.py`, `adapters/cal/google.py`, `cli/cmd_calendar.py` | ✅ done — 1 finding, fixed | Claude 2026-08-27 |
| 4 | Lesson summary | `music-class-summarizer/` | `pipelines/{staging,publish}.py`, `cli/cmd_lesson.py`, `render/` | 🟡 partial — 2 findings (1 fixed, 1 open), see notes | Claude 2026-08-27 |
| 5 | Send to LINE | `send-lesson-line/` | `pipelines/send.py`, `cli/cmd_send.py`, `adapters/chat/` | ✅ done — 3 findings, open | Codex 2026-08-27 |
| 6 | Send recording / YouTube link | `send-recording-line/`, `send-youtube-line/` | `baton send recording`; **no `send youtube` yet** | ✅ done — 3 findings, open | Claude 2026-08-27 |
| 7 | Course archive + clear | `notion-clear-student/` | `cli/cmd_course.py`, `domain/archive.py` | ✅ spot-checked — no gap | Claude 2026-08-27 |
| 8 | Student records | `student-management/`, `student-lookup/` | `cli/cmd_learner.py`, `adapters/db/` | ✅ done — 2 findings, open | Claude 2026-08-27 |
| 9 | Prep report / notes | `student-management/scripts/prep_report.py`, `song_manager.py` | `cli/cmd_prep.py`, `cmd_notes.py`, `domain/prep.py` | ✅ done — 2 findings, open | Codex 2026-08-27 |
| 10 | Retired skills | `archived-skills/{get-latest-lesson,list-completed-lessons,list-inprogress-lessons,student-works}` | `baton learner`, `calendar in-progress` | ✅ done — 4 findings, open | Codex 2026-08-27 |

Legend: ✅ done · 🟡 partial (notes say what is left) · ⬜ unclaimed

---

## Findings

### F1 — `concat` fed unnormalised clips ✅ fixed in 0.2.8

**Lane 1.** `_args()` built `concat` first and applied the scale chain after
it, and the live profile (`auto`) had no branch at all, so nothing normalised
anything. `concat` the filter tolerates mixed codecs but still requires one
frame size and SAR across segments. A session filmed partly in landscape and
partly in portrait — ffprobe reports both as 1920x1080; the display matrix
makes two of them decode to 1080x1920 — failed to configure the filter.

The old `video_tools.combine_clips()` did a separate `normalize_orientation()`
pass per clip first. That pass was dropped whole in the rewrite, taking
`setsar=1`, frame-rate normalisation, audio reconciliation, HDR tone-mapping,
and the iPhone edit-list flags with it.

Fixed: each input gets its own chain before the concat; `auto` now means
"reconcile only when the clips disagree", so a uniform session encodes exactly
as before. `tone_map_hdr` and `fps` are new config keys.

### F2 — the reported ffmpeg line was the last, not the first ✅ fixed in 0.2.8

**Lane 1.** `_one_line()` took the final stderr line. ffmpeg's diagnosis is the
*first* line; the rest is the pipeline unwinding. The job record for F1 read
`ffmpeg failed: Error while processing the decoded data for stream #2:1`,
which names neither a cause nor a clip. Fixed, with the real stderr now
carried in the error's `details`.

### F3 — Google Calendar had no retry and leaked vendor exceptions ✅ fixed in 0.2.9

**Lane 3.** `create`, `list_between`, and `health` called `.execute()` raw.
Two consequences:

- No retry. The old `gcal.py` had `gcal_api_call_with_retry` — exponential
  backoff over 429/403/5xx. Every other Baton adapter retries (Notion and LINE
  through `core.retry.http_request`, Drive and YouTube through `num_retries`);
  calendar was the only one that did not.
- A `googleapiclient.HttpError` from `create` is not a `BatonError`, so it
  escaped the CLI's handler as a traceback rather than an exit code — the one
  thing `doctor` explicitly guards against, and unguarded everywhere else.

This lands hardest on `book`: by the time `create` runs, the session document
is already marked in progress. The ordering in `pipelines/schedule.py` exists
so the two records cannot disagree, and a transient 429 here was the one case
that could still split them.

Fixed: all calls go through `_calendar_call`, which retries transient faults
with the shared backoff and maps everything else to `UpstreamError`. Attempts
configurable via `calendar.google.attempts`.

### F4 — published summaries lost their AI disclosure ✅ fixed in 0.2.9

**Lane 4.** Every summary the old pipeline published carried a dated
disclosure built by `music-class-summarizer/scripts/ai_footer.py` — who wrote
it, when, and that it came from an assistant. `render/summary.py` has no
footer, so every summary published through Baton since 2026-08-23 has gone to
parents without it.

Baton knew the footer existed: `docs.summary_footer` is a regex in
`domain/prep.py` used to *strip* it out of previous summaries when gathering
context. It strips a thing it never writes.

The YouTube description renderer (`render/youtube.py`) does append its own
disclosure, which is how this stayed invisible — the videos carried it, the
pages did not.

Fixed: `summary.footer` config (lines, date format, era, month names), a new
`domain/footer.py`, rendered into both `to_blocks` and `to_markdown`. Empty by
default so no other studio gets a disclaimer it did not ask for. Month names
are configured rather than left to `%B`, which renders English wherever the
studio's locale is not installed.

`prep` now strips the footer using a pattern **derived from the configured
lines** rather than a second hand-written regex, so editing the disclosure can
never leave a stale pattern that silently stops stripping it. The legacy
`docs.summary_footer` regex is kept alongside it, for pages the old pipeline
wrote.

**Backfill still owed.** Four summaries were published through Baton before the
fix and their Notion pages carry no disclosure:

| Learner | Session | Published |
|---|---|---|
| น้องพร้อม | 12 | 2026-08-23 |
| น้องนะโมกีตาร์ | 1 | 2026-08-23 |
| น้องพอล | 7 | 2026-08-23 |
| น้องปุณณะ | 3 | 2026-08-23 |

Re-publishing with `--force` would append the whole summary a second time, so
these want either a small one-off script that appends the three footer blocks,
or a deliberate decision to leave them. Owner's call.

### F5 — a failed YouTube description update has no retry path ✅ fixed in 0.3.3

**Lane 4.** `lesson publish` updates the video description as a best-effort
step after the document is written. If it fails, the failure is recorded on
the draft and reported — but a re-run hits the "already published" gate and
returns OK without retrying, and `--force` would re-append the whole summary
to the page.

The old pipeline tracked `push_state` per service: `push --all` skipped Notion
when it was already `ok` and retried YouTube up to three times. Baton's
per-target `record_target` holds the same information but nothing acts on it
for the YouTube target.

Fixed, in the wide shape: the resume branch of `lesson publish` retries the
description update in both cases it can be owed — a previous attempt that
failed with attempts left, and a recording that landed on the document *after*
the summary was published, which nothing had ever recorded as pending. The
blocks already on the page are never touched, so there is no second copy and
no need for `--force`.

Two places remember the outcome, mirroring the docs-completion check the
module already had: the draft (wiped by every re-stage) and the published
record (`note_youtube`), so re-staging to correct a title does not re-update
the video. Attempts are capped by `media.youtube.description_attempts`
(default 3) and counted across both memories, so a video Baton will never own
is not retried forever.

### F6 — the default send gate no longer requires the YouTube link ✅ fixed in 0.3.2

**Lane 5.** The old gate refused to send unless the Notion page, exact-week
`line_summary`, week number, **and YouTube link** were all present. It checked
that twice: `_gate_check` in `send.py`, then the direct-call guard in
`services/line/push.py`. `DATA-003` records why this became fatal: a summary
without the recording had already reached the studio.

Baton has the same `video_link` field and gate mechanism, but its packaged
profile puts `video_link` in `send_lesson_optional`. A Notion outage or a page
whose recording has not landed therefore produces a warning and still sends.
That is the exact incomplete-message path the old hardening closed. The code
comments say the legacy fail-closed behaviour is preserved exactly, so the
optional default is also a silent degradation, not an intentional removal.

Proved in the gateway: legacy `_gate_check` returns `youtube_link` as missing
for an otherwise-complete lesson. Baton's
`test_a_complete_context_passes_and_warns_about_the_optional_gaps` proves the
same context passes. The studio profile inherits the packaged gate lists; it
does not override them.

Fixed: `video_link` is back in `gates.send_lesson_required` in the packaged
defaults, matching the legacy studio. A studio that does not record lessons
moves it to the optional list — the test suite covers both stances, including
that no `Messenger.send` occurs under the required stance when the document
cannot be read.

### F7 — a batch can send the same learner twice through an alias ✅ fixed in 0.3.2

**Lane 5.** The old batch resolved every requested name first, compared the
canonical learner names, and refused the whole batch when two inputs resolved
to one learner. Its test pins the real case: `เจ` plus `น้องเจ` must not
produce two LINE messages.

Baton checks `len(set(requested))` before resolution, then resolves and sends
each entry independently. Two distinct strings that map through `db.aliases`
to the same learner pass the check. A focused reproduction returned exit 0,
`sent: 2`, with both results naming the same canonical learner; the legacy
alias-duplicate unit test refused the same shape before any send.

The LINE retry key does not save this case. Each call composes a message again
and chooses random opening/closing phrases, so the message text (and therefore
the idempotency key) may differ. These are two requested sends, not an HTTP
retry.

Fixed exactly as suggested: `handle_batch` resolves every entry first,
refuses the batch on duplicate learner *ids* (naming the person, not the
spelling), and passes the resolved learners into `_send_one` so the check and
the delivery cannot diverge. The test pins the real pair — full name plus
alias — and that nothing was sent.

### F8 — the piece catalogue became read-only 🔴 open

**Lane 9.** `song_manager.py` was a full catalogue editor: it added songs,
updated the title and all three links, cleared links deliberately, and deleted
unused songs. Delete first queried the learners using that song and refused
with their names until they were reassigned. These were advertised triggers
in the old skill (`เพิ่มเพลง`, `ลบเพลง`, `แก้ชื่อเพลง`, practice track, and
sheet link), not incidental helper functions.

Baton models pieces and their links, but the store protocol exposes only
`list_pieces` and `get_piece`; the CLI exposes only `learner pieces` and
`learner assign`. There is no create, update, or delete path in either DB
adapter. Since the old skill is retired, a teacher can still assign an
existing catalogue row but cannot add the next song, correct its title, attach
or clear a practice/sheet/original link, or safely remove an obsolete row
through Baton.

Suggested shape: add store methods and `baton learner piece add|update|delete`
(or a top-level `piece` group). Carry over partial-update semantics — an
explicit empty link clears it — and the pre-delete assignment gate, including
the learner names that block deletion.

### F9 — catalogue relationship reports were dropped 🔴 open

**Lane 9.** The old `--info SONG_ID` returned the song's links and the learners
assigned to it; `--assignments` joined every learner to the current song and
listed both assigned and unassigned learners in one call. `--search` also
looked up titles without requiring the numeric id. The skill had explicit
triggers for “น้องXเล่นเพลงอะไรอยู่” and “song assignments”.

`baton learner pieces` returns only catalogue rows. `learner show` can answer
one learner at a time, but no Baton command performs the reverse lookup (“who
is using this song?”), emits the whole assignment matrix, or searches the
catalogue by title. Rebuilding those joins by hand is a regression from the
single script that made them consistent, especially before changing or
deleting a catalogue row.

Suggested shape: add `piece show/search/assignments` queries (or enrich a
single catalogue command) and test both sides of the relationship, including
learners whose `current_piece_id` points at a missing row.

### F10 — lesson messages lost the studio's Thai date format ✅ fixed in 0.3.2

**Lane 5.** The old sender converted an ISO lesson date to a Thai family-facing
date with abbreviated Thai month and Buddhist Era year. In the gateway,
`format_date_thai("2026-08-23")` returns `23 ส.ค. 2569`.

Baton reads the document date fresh but passes it straight into
`compose_message`; the same input appears in the LINE header as `2026-08-23`.
The rest of the message was deliberately matched byte-for-byte to the old
`push.py`, so this visible date regression is a lost formatting detail rather
than a generic-studio decision recorded in configuration.

Fixed: a shared `domain/localdate.py` (a `chat.date` config block — format,
era, month names) formats the document date before either message is
composed, and the footer's stamp is built on the same formatter, so the era
decision cannot come to differ between the page and the message. Unparseable
free-form dates pass through unchanged. Verified in the gateway:

```
🥁 สรุปการเรียนของน้องพร้อม (กลอง) - 23 ส.ค. 2569
```

The studio's profile sets `chat.date` to abbreviated Thai months with the
Buddhist era, matching the old `format_date_thai`.

### F11 — `learner latest` drops unrecognised summary sections 🔴 open

**Lane 10.** `get-latest-lesson` returned the page's whole readable summary:
every paragraph, heading, list, and to-do, including nested blocks. Baton
`learner latest` instead feeds the blocks through `SectionRules`, which keeps
only content below a configured heading such as overview, content, focus, or
homework.

Baton's own summary contract and renderer support `extra_sections`, but those
headings are not automatically added to `docs.sections`. Proved with an
`Ensemble notes` heading and `Counted the band in` paragraph: the legacy
extractor returned both lines; Baton's default section reader returned empty
named sections. A published detail can therefore be visible on the document
but absent from the command intended to answer "what happened last time".

Suggested fix: have the latest-session payload carry both the configured prep
sections and an ordered remainder/extra-section representation, or expose a
separate full-page summary field. Keep prep's bounded named sections for its
own gate.

### F12 — there is no all-completed-lessons report 🔴 open

**Lane 10.** `list-completed-lessons` answered the studio-wide questions
"how many are done?" and "who is done?" in one call. It sorted every completed
lesson by date, supported learner and date-range filters, and included the
instrument, page link, and a ten-line summary preview. Its Notion fallback was
fixed specifically to traverse every page per learner rather than one row.

Baton can show one learner's sessions and can report who is still in progress,
but the `learner` command has no completed/list-history counterpart across all
learners. Reconstructing the report requires an external loop over every
learner and every session document, losing the one-command filtering and
partial-failure behaviour the retired skill supplied.

Suggested shape: add a read-only `learner completed` or `lesson list` command
with `--learner`, `--from`, and `--to`, backed by one bounded cross-learner
query and carrying unreadable rows instead of dropping the whole report.

### F13 — the in-progress report lost recording readiness ✅ fixed in 0.3.5

**Lane 10.** The old in-progress report did more than name unfinished pages:
it scanned every block page (with pagination and a 24-hour cache) and showed
whether a YouTube recording was already present. It rendered the result as the
PNG table the skill explicitly required the agent to send, so the teacher saw
week, date, recording readiness, and title together.

Baton's calendar-window algorithm is a better answer to "who still owes a
summary" and avoids the old whole-world scan, but its payload contains only
learner/session status data. It never lists blocks and has no `has_youtube`
field or report asset, so the studio can no longer see which unfinished
lessons already have their recording ready from this workflow.

Fixed as suggested: `learner in-progress --videos` reads only the calendar
window's candidates and adds a `video_link` field to each row (with a 🎬/—
marker in the human report). Without the flag the report stays exactly as
cheap as it was — no block reads. The link recognition is the F18 reader, so
bookmarked recordings count. The old PNG table remains deliberately
unported: the machine-readable field was the capability, and the agent that
relays the report can render it however the teacher asks.

### F14 — recorded works became append-only and lost their metadata 🔴 open

**Lane 10.** `student-works` supported add, per-learner/global list, title/type
search, update, and confirmed delete. Its row carried an instrument override,
Drive and YouTube links, notes, tags, and a difficulty/rating alongside the
date and work type.

Baton exposes only `learner works` and `learner add-work`. `Work` models the
title, type, both links, and date, but not instrument, notes, tags, or rating;
although an adapter retains the database row in `raw`, `to_dict` hides those
fields and no write path accepts them. There is also no update, delete, or
search method in the store protocol. Existing rich rows are therefore only
partly visible, and a correction or tag change must bypass Baton.

Suggested shape: first model and map the optional legacy fields so reads are
lossless, then add `work search|update|delete` with partial-update semantics,
link validation, dry-run, and a confirmation gate for delete. The old
`google_drive_link` is already preserved by this studio's `drive_link`
mapping; that part is not a gap.

### F15 — every Baton-published page reads back with an empty `content` ✅ fixed in 0.3.2

**Cross-lane (4 and 9).** Found while cross-checking the other lanes, which is
why neither lane caught it: the writer and the reader are in different lanes
and each looked correct on its own.

Two config trees name the same sections and do not agree:

| | key | heading / keywords |
|---|---|---|
| writer, `summary.sections` | `covered` | `What we covered` |
| reader, `docs.sections` | `content` | `เนื้อหา`, `สิ่งที่เรียน`, `Core Lesson` |

`SectionRules.read()` matches a heading by casefolded substring against that
keyword list. Nothing in it matches `What we covered`, so the largest section
of every summary Baton publishes — what was actually taught — reads back
**empty**. `overview`, `focus`, and `homework` happen to match and hide it.

`prep.required` includes `content`, and prep is fail-closed by design: a
learner whose page is missing a required field is blocked rather than reported
half-read. So the teacher gets **no pre-lesson briefing at all** for any
learner whose last summary came from Baton.

Proved in the gateway against real pages, and the correlation is total:

```
$ baton prep --learner ... --json
ready  : ['น้องอิคคิว', 'น้องขิงขิง']          ← last summary written by the old skill
blocked: น้องพร้อม       -> ['content']        ← published by Baton
blocked: น้องพอล         -> ['content']
blocked: น้องปุณณะ       -> ['content']
blocked: น้องนะโมกีตาร์  -> ['content']
```

Every Baton-published learner is blocked; every legacy-published one passes.
It also silently starves `lesson stage`'s previous-session context and
`learner latest` of the same section, which is the sharp end of F11.

This is F4's shape a second time: a writer and a reader that are supposed to
describe the same document, wired through two independent config keys with no
mechanism keeping them in step. Patching the keyword list alone fixes today's
symptom and leaves the trap armed — renaming a heading in `summary.sections`
breaks prep again, silently.

Fixed: a new `domain/sections.py` holds both vocabularies and the explicit
mapping between them (`WRITTEN_HEADINGS`, `READ_KEYWORDS`, `WRITES_INTO`), and
`SectionRules.from_config` folds every configured written heading into the
keywords of the section it feeds — the same derive-don't-duplicate move as the
footer's strip pattern. The old pipeline's Thai keywords keep their priority,
so legacy pages read exactly as before. A round-trip test suite publishes a
summary through the renderer and reads it back through the reader, which is
the test whose absence let this through. Verified in the gateway: all four
Baton-published learners prep-ready again.

Worth checking as part of the fix: `next_goal` is in `prep.warning` and is
never written by Baton at all, and `practice_goals` reads empty because the
goals render as `to_do` blocks that `homework_types` claims first. The second
is by design; the first may be another gap.

### F16 — recording messages lost the session-document link ✅ fixed in 0.3.3

**Lane 6.** The old `send_recording.py` ended every recording message with
"📝 รายละเอียด Notion: \<url\>" — the learner's latest finished session page,
fetched live through `student_lookup.get_latest_lesson_for_student` and
attached fail-open (a fetch failure still sent the message). The v4 changelog
records adding it on purpose: a parent tapping a recording could continue into
the lesson it came from.

`compose_recording` (`pipelines/recording.py`) sends the header, the title,
type and date, and the two link labels — nothing else. No code path fetches a
document URL for a recording, so the message is a dead end where it used to
lead somewhere.

Fixed exactly as suggested: `handle_recording` reads
`PublishedRecord.latest(learner.id)`'s `doc_url` under `contextlib.suppress`
and `compose_recording` appends the `📝 รายละเอียด Notion:` line — last line
of the message, never a gate, absent when nothing is published.

### F17 — there is no way to send the latest lesson's video by itself ✅ fixed in 0.3.5

**Lane 6.** `send-youtube-line/scripts/send_youtube.py` was a one-command
workflow with its own trigger vocabulary ("ส่งวีดีโอ/ลิงก์ youtube ของน้องX",
kept deliberately separate from "ผลงาน/record" and "สรุปการเรียน" in the old
skill registry): resolve the learner, take the latest finished session, scan
its blocks for a YouTube URL, and send a **video-first** message — instrument
header with week and Thai date, the lesson title, a ~150-character summary
snippet read from the page, and the link. No video on the page was its own
explicit refusal: "ยังไม่มีวีดีโอสำหรับสัปดาห์นี้".

Baton's nearest answer is `send lesson`, which sends the whole published
summary with the video line at the bottom — a different message answering a
different request, and it re-sends the full summary to a parent who asked
only for the video. Every ingredient exists (`find_video_link`,
`PublishedRecord.latest`, the F10 date format, `learner latest`'s section
reader); no command composes them.

Fixed as suggested: `baton send video <name> --to <contact> [--session N]
[--dry-run]` in a new `pipelines/lesson_video.py`. Instrument header, session
label and Thai date (`chat.date`, same as the lesson message), titles, a
~150-character taste of the summary from the same section reader (whole
lines, ellipsis cut), and the link last. Deterministic — no varied phrasing —
because a re-send of a lost link should read like the first one. Fail-closed
with its own refusal ("has no video on it yet") when the session has no
recording, distinct from `send recording`'s work refusal.

### F18 — `find_video_link` recognises only `video` blocks ✅ fixed in 0.3.3

**Lane 6; sharpens lane 5's gate.** The old readers accepted a YouTube URL in
three block shapes: `get_youtube_url_from_page` matched `video.external`,
`bookmark`, and `embed`, and the in-progress readiness check additionally
scanned `rich_text` links inside paragraphs and list items. That tolerance
was load-bearing: Notion's UI turns a pasted URL into a bookmark, so a video
link added by hand rather than by the pipeline is a bookmark.

`find_video_link` (`adapters/docs/base.py`) matches `block.type == "video"`
only. A page whose only video link is a bookmark or an embed reads as having
no recording: the "เฉพาะ Video:" line goes missing from `send lesson`, the
YouTube description step sees nothing — and since F6 moved `video_link` into
the required gate, such a page now **blocks the send entirely** while looking,
to a person reading the Notion page, like it has its video.

Fixed: `find_video_link` accepts `video`, `bookmark`, and `embed` shapes
(`docs.video_link_blocks` overrides), with the legacy reader's exact
tolerance kept — outside `video` blocks only video-host URLs (youtube /
youtu.be) count, so a bookmarked sheet or article is still just a link, and a
`video` block keeps accepting any host (a Drive-hosted file is a recording
too). Newest-last ordering preserved across shapes.

Checked against the live pages before fixing: all four currently carry a
pipeline-written `video` block, so no send was blocked yet — the trap was
armed for the first hand-added link.

### F19 — there is no way to onboard a learner 🔴 open

**Lane 8.** `add_student.py` did the whole first day: insert the learner
(name, instrument, tone, has_instrument, prompt_level) behind a
case-insensitive duplicate gate, then register their session pages — parse
Notion URLs in all three shapes the studio actually sees (`notion.site/5-…`,
`notion.so/workspace/…`, `app.notion.com/p/…`), auto-detect the week from the
URL's numeric prefix or `W<n>-url` tokens, and insert the `student_pages` rows
carrying `database_id` plus the full link; optional `master_link` update;
`--dry-run` and an interactive confirm before any write.

Baton has no counterpart at any layer: `cmd_learner` exposes
list/show/sessions/latest/next/in-progress/works/add-work/pieces/assign, and
the `LearnerStore` protocol has no create method. `prompt_level` and
`master_link` are not modelled on `Learner` at all. Every new learner still
needs the old script or hand-written inserts.

Two hard-won details worth carrying into a port: the learner id is computed
client-side (the table does not auto-increment), and the script forces it
above a fallback-safe floor when *that response* was served by the fallback
mirror — checked per-response rather than by the sticky fallback flag,
because the flag stays set after Supabase recovers mid-run. The duplicate
gate is case-insensitive exact, not fuzzy.

Suggested shape: `baton learner add "<name>" --instrument … --tone …
--has-instrument` with repeatable `--page-url` (week auto-detected from the
URL prefix, sequential fallback) and `--db-link`, `--dry-run` before the
real write. The id-floor logic belongs in the fallback adapter, not the CLI.

### F20 — a hand-recorded work never reaches the session page ✅ fixed in 0.3.5

**Lane 8.** `push_recording_to_notion.py` put a recording onto the learner's
page: a "🎬 ผลงาน Record" heading, one bold title paragraph per work, a
YouTube `video` block and a Drive `bookmark`, **clearing previously
auto-pushed video/bookmark blocks first** so a re-push replaced instead of
appended. It targeted the In-progress page, a `--week` the caller names, or
the latest Done page as a fallback, and could set Status → Done.

In Baton the pipeline path is covered — `pipelines/video.py` appends the
video block behind an `_already_linked` guard — but that is the only writer.
`learner add-work` records the DB row and writes nothing to any page, so a
recording that did not go through the pipeline (a teacher's own edit, a clip
shared directly) has no command that links it to the session, and the Drive
side of a recording never lands on a page at all. Closing the session is
deliberately publish-only now; the page-presentation gap is the durable part.

Fixed as `baton learner attach-work <name> [--pick N] [--session N]
[--dry-run]`, two-step like `send recording` (without --pick it lists the
works and asks). It writes the old section shape — the 🎬 heading, bold
title, YouTube side as a video block, Drive side as a bookmark — onto the
session In progress by default. The idempotency rule is the URL, not the old
clear-everything: the video pipeline now writes the lesson's own recording
onto the same page, and clearing all video/bookmark blocks would take it
off. A link already on the page is never written twice, and nothing else on
the page is removed. Session-closing stays with publish.

---

## Notes on partially-audited lanes

### Lane 9 — prep report / notes, checked and otherwise equivalent or better

- `baton prep` preserves the old hard gate exactly: week, date, titles, page
  link, overview, content, and homework are required; next goal is a warning.
- Calendar-based discovery and repeatable explicit learners are preserved.
  Baton's configured title parsing also reports unmatched calendar events
  instead of silently losing them.
- Heading-based section parsing, paginated block reads, checklist-to-homework,
  practice-goals fallback, footer stripping, and the 400-character cap are
  carried over. Document read failures now name the unreadable page rather
  than looking like empty sections.
- The report remains the source of truth and is carried verbatim in JSON as
  `report`; a partially-ready day returns the ready entries plus every blocked
  learner, while an all-blocked day exits through the gate.
- The old `original_link` column is not lost: this studio maps it to Baton's
  `Piece.source_link`. Practice-track and sheet links are also modelled and
  shown in the JSON catalogue; F8 is the missing mutation surface, not missing
  storage.
- `baton notes` is independent of the song catalogue. Its deterministic
  Markdown conversion, preview, chunking, title derivation, and document-store
  error mapping are additive; no prep-report behaviour was found missing there.

### Lane 10 — retired skills, checked and otherwise equivalent or better

- Latest means the newest **Done** document by its date, not the highest week;
  Baton preserves that rule, adds a numeric tie-breaker, and never guesses a
  partial learner name. The current payload still carries week, title, date,
  status, document id, and URL.
- Today's teaching-schedule mode moved to `baton prep`: calendar discovery,
  latest lesson context, homework, partial failures, and a whole-day report are
  preserved there with a fail-closed readiness gate.
- Baton's in-progress discovery uses the recent calendar window and then
  checks the exact booked document. This intentionally drops stale/future
  pages and reports unmatched events and unreadable pages; it is more precise
  than scanning the ten newest pages of every learner. F13 is only the missing
  recording-readiness column/presentation.
- Document and DB reads share the adapters' pagination, bounded concurrency,
  retry, and error mapping instead of each retired script carrying a divergent
  request loop.
- Recorded-work listing remains newest-first and both YouTube and Drive links
  survive this studio's schema mapping. F14 covers the mutation/search and
  rich-metadata surface that did not survive.

### Lane 5 — send to LINE, checked and otherwise equivalent or better

- The message shape, instrument icons, random openings/closings, published
  short summary, Notion link, practice track, and direct video line match the
  old `push.py` format. F10 is the date-format exception.
- LINE transient retries are preserved through `core.retry.http_request`; the
  deterministic `X-Line-Retry-Key` is also preserved, so a lost response does
  not duplicate a delivery.
- Contact and learner resolution are stricter: exact name or explicit alias,
  never the old shortest partial match.
- One failed learner does not abandon the rest of a batch; the aggregate names
  every blocked result and returns the gate exit code. Raw duplicate names are
  refused; F7 is the canonical-name hole that remains.
- `--dry-run` runs the real gate and composes the exact message without
  delivery. The current agent skill also keeps the no-bypass rule.
- The legacy skill always inserted a separate confirmation prompt between
  preview and send; the Baton workflow treats an explicit send request as the
  authorization and recommends `--dry-run` only for the first send that day.
  Recorded as a visible workflow change, not a finding: no data capability is
  lost and the strict learner/contact gates remain. If the studio still wants
  a second confirmation for every explicit send, put it back in the agent
  skill or wrapper rather than pretending the CLI enforces it.
- The studio-specific ops ledger is preserved outside the package by
  `workspace/scripts/baton_send_lesson.py`; `AGENTS.md` requires that wrapper,
  and the 21:05 reconcile reads Baton's published records. A ledger write
  failure remains fail-open for delivery and visible as `ledger_status`.
- `daily_send_to_prao.py` was deliberately retired rather than silently
  omitted. The standing workflow uses an explicit Baton batch plus the nightly
  published-vs-delivered reconcile, and the deprecated skill forbids running
  the old auto-send script.

### Lane 2 — video: Drive, YouTube, orchestration

Spot-checked and found equivalent, not exhaustively audited:

- Resumable chunked upload with `num_retries=3`, title truncated to YouTube's
  100 characters, `selfDeclaredMadeForKids` — parity with `youtube_uploader.py`.
- Ownership check before `update_description` — carried over, and the
  docstring records the 2026-08-09 incident it came from.
- Deferred trashing (sources are only moved after everything else for that
  learner succeeded) — parity with `_process_student_group`.
- The `getaddrinfo_ipv4_fixed` monkey-patch in both old scripts has no Baton
  equivalent, and does not need one: IPv6 is disabled inside the container
  (`/proc/sys/net/ipv6/conf/all/disable_ipv6` is 1), which is what the patch
  was working around.

**Still to check:** `pipeline_orchestrator.py` against `pipelines/video.py`
step by step; `drive_cleanup.py`'s rules for what counts as a generated
artifact versus a source clip; `preflight_drive.py`; `check_week_state.py` and
`check_trash_state.py`; the watcher loop's behaviour when a download partially
fails.

### Lane 4 — lesson summary

Checked and equivalent or better:

- Publish ordering (append, then delete) and the preserve policy — mechanism
  where the old system had a standing warning in prose.
- Per-target state and the published record, versus the old `push_state`
  (except F5).
- `tone` and `has_instrument` carried through the models and both DB adapters.
- `--dry-run` on publish, via `SummaryPublisher.plan`.
- Theory callouts resolved from the studio's own store, not written by the model.

**Still to check:** `services/blocks.py` block-building against `render/`
(especially code-fence language handling and any block type the old one emitted
that the new schema has no field for); `staging.py` versus
`pipelines/staging.py` for the atomic-write and `.bak` behaviour;
`edit_summary.py`'s editing surface versus `lesson ingest`; the
`line_summaries.json` cache format that the reconcile cron still reads.

### Lane 3 — calendar, checked and no gap

Recorded so nobody re-checks them:

- Name resolution is a stricter gate in Baton (`domain/resolve.py`): exact,
  then a single alias hop, and *never* an automatic partial match however few
  candidates there are. `scal.py` had the same intent.
- `parse_schedule` handles `17.00`, `17:00`, and `17`; free markers still bound
  the previous slot rather than extending it; a malformed line raises instead
  of being silently skipped.
- Document-before-event ordering on book, and the reverse on cancel.
- A duplicate booking of the same session on the same day is refused — the old
  `_find_duplicate_event` matched on title alone.
- Cancels outside `rollback_window_days` are refused. The old skill left this
  to the agent's discipline in `SKILL.md`.
- Instrument emoji, Thai date shorthand (`วน`, `พน`, `มร`, and the long forms)
  are configured in the studio profile.

**Known non-gap, worth writing down:** `baton calendar` has no equivalent of
`gcal.py add` for a non-student event (a run, a meeting). That is not a
regression — `AGENTS.md` still routes general calendar work to `gcal.py` — but
it is the one thing blocking the calendar lane from being switched over to
Baton entirely.

### Lane 6 — send recording, checked and otherwise equivalent or better

- The two-invocation list-then-pick design is stricter than the old
  validation, not looser: `validate_student.py` silently took the first
  `ilike` match (shortest name) as *the* student; Baton ends at exit 3 with
  the candidates and makes a person choose.
- The message keeps the old plain-text shape (header, instrument icon,
  per-work title, YouTube/Drive labels) and adds the work type and performed
  date — Thai-formatted since the F10 fix. Deterministic phrasing, unlike
  `compose_message`'s random openings, is deliberate; so is refusing a work
  with no links at all, which the old script would have announced as an
  empty recording.
- LINE delivery retries (3 attempts, exponential backoff) are preserved by
  the shared `core.retry.http_request` path lane 5 already checked; the
  `recipients.json` alias table became `chat.contacts` + `send contacts`.
- Read-side fields the old validator returned (notes, tags, difficulty) are
  F14's loss, not this lane's.
- One visible workflow change, recorded here rather than as a finding: the
  old skill sent several works as one message (multiple 📌 sections, and a
  Flex carousel before v4); Baton's rule is one pick one work, so "send all
  of X's covers" arrives as several messages. The links still arrive and the
  rule is written down in the agent skill — if the studio wants combined
  delivery back, that is a feature to ask for, not a regression to restore
  silently.

### Lane 8 — student records, checked and otherwise equivalent or better

- `student_lookup.py`'s read surface maps onto `learner latest / sessions /
  next / in-progress / works` with the gaps already recorded as F11–F14. Its
  next-empty rule was itself ported *from* Baton (the script's header cites
  commit 6fcd410 and the owner's 2026-08-17 decision), so parity there is by
  construction, including the stale-In-progress skip.
- `update_notion_status.py` became `calendar book`: marking the page In
  progress with its date *before* the event exists is a stronger ordering
  than the old book-then-run-a-second-script sequence. One behavioural
  difference, not a gap: the old script copied the previous week's Titles
  onto the newly booked page; Baton writes titles at stage/publish time and
  `complete()` only fills blanks. Nothing in Baton reads an in-progress
  page's Titles, and the lesson contract carries the previous session's
  summary, which subsumes the carry-forward.
- `--schedule` (today's teaching order with homework) is `baton prep` —
  lane 9.
- The old lookup's name matching (exact → prefix-stripped → `ilike`, auto-pick
  shortest) is the loose resolution every other lane replaced with
  exact-or-alias; stricter is the gate, not a loss.

---

## Verifying a fix

Full suite, lints, and types:

```bash
cd ~/studio-baton
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/baton
```

Deploying to the running gateway — **both installs, or the fix silently does
not apply**:

```bash
uv build --wheel
docker cp dist/studio_baton-<version>-py3-none-any.whl openclaw-gateway:/tmp/
docker exec -u root openclaw-gateway python3 -m pip install \
  --no-deps --force-reinstall --break-system-packages /tmp/studio_baton-<version>-py3-none-any.whl
docker exec openclaw-gateway /home/node/.openclaw/baton-venv/bin/python3 -m pip install \
  --no-deps --force-reinstall /tmp/studio_baton-<version>-py3-none-any.whl
docker exec openclaw-gateway baton --version   # expect the new version
```

> ⚠️ `docker-compose.yml` pins `studio-baton[google]` to an exact version from
> PyPI (`0.4.1` as of 2026-08-27). Any
> image rebuild reverts every hand-installed wheel. Publish to PyPI or repoint
> that build arg before rebuilding.

---

## Observed in passing, outside this audit's scope

`baton doctor` reports 21 checks with one failing, and it is not a migration
gap — the Supabase schema has drifted from what the profile expects:

```
Database is reachable and every table resolves — supabase rejected the query:
column student_works.drive_link does not exist
```

Same shape as the drift already recorded against the students fallback. Worth
its own look; it is not something the rewrite dropped.
