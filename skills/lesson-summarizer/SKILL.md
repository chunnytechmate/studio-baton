---
name: lesson-summarizer
description: "Write and publish a lesson summary. Use after a lesson when asked to summarise it, write it up, or push it to the student's page. You supply structured JSON; Baton renders the document."
---

# Lesson summarizer

You write the summary. Baton renders and publishes it. You never write the
document itself, and never a Notion block.

## Triggers

- "summarise the lesson with X", "write up X's session"
- "publish X's summary", "push the summary"

## The loop

```bash
# 1. Start a draft with the teacher's notes
baton lesson stage "<name>" --context "<what happened>" --json

# 2. Get the schema and everything you need to write against
baton lesson contract "<name>" --json

# 3. Write JSON matching `schema`, then submit it
baton lesson ingest "<name>" --file summary.json --json

# 4. Show the rendered result to the teacher
baton lesson render "<name>"

# 5. Publish once they are happy
baton lesson publish "<name>" --json
```

`baton lesson list`, `show`, `remove` inspect and discard drafts.

Each takes the learner positionally or as `--learner "<name>"`, but not two
different names at once. `publish --session N` does not choose a lesson — a
learner has one draft at a time — it refuses if the draft is for another one.

## Writing the summary

`contract` gives you the schema, the lesson notes, the teaching profile, and the
callout ids that exist. Return **one JSON object and nothing else** — no prose
around it, no markdown fence.

- Base every statement on `lesson_notes`; invent no progress it does not
  describe. Use `previous_session_summary` to judge what is new, not to repeat.
- Use only callout ids from `available_callout_ids`; never write theory text.
- The `short_summary` is what a parent reads. No emoji, no links, one line per
  field. These are validated, not requested.

**One fact belongs to one section.** Each answers a different question, and a
fact stated in more than two of them is rejected with a pointer to the third.

| Section | Answers |
| --- | --- |
| `overview` | How did the session go? |
| `progress` | What is different from last time? |
| `covered` | What was worked on? |
| `focus` | What is still hard, and what will be done about it? |
| `goals` | What should they practise at home? |

`progress` is a change, not a rating: `before` and `after` — "needed the count
called out" → "counts through unaided". Required once a lesson has a previous
session, never on a first one. If nothing changed, say so as the one entry.

Things the validator rejects by name:

- **A rating where an observation belongs.** "Did very well" survives any
  lesson, so it describes none. Say what they managed and how much help it took.
- **A word about the child rather than the playing.** "A weak point" is
  something someone *is*; "still slow from the page" is something a lesson
  changes. Families keep these pages.
- **A goal nobody can practise at home.** What can only happen in the next
  lesson belongs in `focus`; "be more open to reading" is an attitude, not an
  action. Say what to do, and how long or how many times.

The last instruction in `contract` is written for *this* learner — their tone,
and whether they own an instrument. Follow it over your own instincts about
register: it comes from their record, not from the notes.

## Rules

**Never publish without showing the render first**, unless asked to run the
whole thing unattended.

**Publishing twice is refused** — a second publish would leave two summaries on
the page. Use `--force` only if the user explicitly asks to replace it.

**Publishing ends the session.** It marks the page done and fills the date and
repertoire columns if empty, so the next summary goes to the next session and
`prep` can brief this one.

**Exit 6 after a publish means the summary landed but the session is not
closed.** Re-run `baton lesson publish`: it appends nothing the second time and
only finishes the session. Say so rather than reporting the summary as lost.

**Publish repairs a recording the pipeline uploaded but never linked.** A run
that dies after uploading leaves the video on YouTube and the page without it,
and `send` then refuses. Publish appends the block itself — a re-run of an
already-published lesson included. Report `recording` (`linked` with the URL,
or `error`); never add the block by hand.

**A song on the page is not the lesson's recording.** The piece being learnt
sits on the same page as a bookmark and an embed, and publish skips those. A
`null` `youtube` beside a visible YouTube link means that link is the song.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Continue to the next step |
| `1` | A draft is missing — `baton lesson stage` first |
| `3` | Show `details.candidates`, ask, re-run with the exact name |
| `4` | Your JSON, not a Baton fault. Fix every `details.violations` entry — each carries a pointer — and resubmit changed content |
| `6` | Report; the draft and summary are kept, so a retry resumes |
