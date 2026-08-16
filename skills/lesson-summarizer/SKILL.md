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

## Writing the summary

`contract` gives you the JSON Schema, the lesson notes, the learner's teaching
profile, and the callout ids that exist. Return **one JSON object and nothing
else** — no prose around it, no markdown fence.

- Base every statement on `lesson_notes`. Do not invent progress that is not
  described there.
- Say plainly what is still difficult, and pair each difficulty with a fix.
- Use `previous_session_summary` to judge what is new, not to repeat it.
- Use only callout ids from `available_callout_ids`. Never write theory text —
  Baton substitutes the studio's own wording from the id.
- The `short_summary` is what a parent reads. No emoji, no links, one line per
  field. These are validated, not requested.

## Rules

**Exit 4 is your JSON, not a system fault.** `details.violations` names each
problem with a pointer to it. Fix them all and resubmit — do not resubmit the
same content, and do not report it to the user as a Baton failure.

**Never publish without showing the render first**, unless the user has asked
you to run the whole thing unattended.

**Publishing twice is refused.** That is correct: a second publish would leave
two summaries on the page. Use `--force` only if the user explicitly asks to
replace it.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Continue to the next step |
| `1` | A draft is missing — `baton lesson stage` first |
| `3` | Show `details.candidates`, ask, re-run with the exact name |
| `4` | Fix every entry in `details.violations`, resubmit |
| `6` | Report; the draft and summary are kept, so a retry resumes |
