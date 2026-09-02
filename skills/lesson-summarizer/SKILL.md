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
- "take that summary back down", "I published it on the wrong page"

## The loop

```bash
baton lesson stage    "<name>" --context "<what happened>" --json  # 1. start a draft
baton lesson contract "<name>" --json                              # 2. schema + context
baton lesson ingest   "<name>" --file summary.json --json          # 3. submit your JSON
baton lesson render   "<name>"                                     # 4. show the teacher
baton lesson publish  "<name>" --json                              # 5. once they are happy
```

`baton lesson list`, `show`, `remove` inspect and discard drafts. Each command
takes the learner positionally or as `--learner "<name>"`, never two names.
`publish --session N` does not choose a lesson (a learner has one draft at a
time) it refuses if the draft is for another one.

```bash
baton lesson stage-set "<name>" --field context --value "<fixed notes>" --json
baton lesson unpublish "<name>" --dry-run --json   # then again without --dry-run
```

`stage-set` amends `titles`, `context`, or `corrected_context`; the summary
itself is only ever accepted through `ingest`.

## Writing the summary

`contract` gives you the schema, the notes, the teaching profile, and the
callout ids that exist. Return **one JSON object and nothing else**, no prose
around it, no fence.

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
| `goals` | What to practise at home, or, for a learner with no instrument there, what the next lesson works towards |

`progress` is a change, not a rating: `before` and `after`: "needed the count
called out" → "counts through unaided". Required once a lesson has a previous
session, never on a first one. If nothing changed, say so as the one entry.

Things the validator rejects by name:

- **A rating where an observation belongs.** "Did very well" survives any
  lesson, so it describes none. Say what they managed and how much help it took.
- **A word about the child rather than the playing.** "A weak point" is
  something someone *is*; "still slow from the page" is what a lesson changes.
- **A goal nobody can practise at home.** What only the next lesson can do
  belongs in `focus`. Say what to practise, and how long or how many times.

The last instructions in `contract` are written for *this* learner: tone,
instrument, prompt level, and whether they own one at home (which also renames
the goals section). Follow them over your instincts about register: they come
from that learner's record, not from the notes.

## Rules

**Never publish without showing the render first**, unless asked to run it
unattended. **Publishing twice is refused**: two summaries would end up on the
page; `--force` replaces, and only if the user asks for it.

**Publishing ends the session**: the page is marked done and the date and
repertoire columns filled if empty, so the next summary goes to the next
session. **Exit 6 there means the summary landed but the session did not
close.** Re-run publish: it appends nothing the second time and only closes the
session. Say that, rather than reporting the summary as lost.

**Unpublish removes only what Baton can prove it wrote** (the blocks the
publish recorded), then sets the session back to in progress, rewinds the draft
to `summarised`, and drops the record. Show `--dry-run` first.

**Exit 3 from unpublish means someone edited the page: nothing was removed.**
`details.candidates` names each block, `edited` or `ambiguous`. Show them and
ask. `--whole-page --force` takes down *everything* on the page, recordings
included, only when the user asks for it, never to get past that exit 3.

**Unpublishing does not un-send.** If the message went out, say so: the family
has read it, and re-publishing does not change that.

**Publish repairs a recording the pipeline uploaded but never linked**, and
skips the song's own bookmark and embed. Report `recording` (`linked` with the
URL, or `error`); never add the block by hand. A `null` `youtube` beside a
visible YouTube link means that link is the song, not the lesson.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Continue to the next step |
| `1` | A draft is missing: `baton lesson stage` first |
| `3` | Show `details.candidates`, ask, re-run with the exact name. From `unpublish` they are edited or ambiguous blocks and **nothing was removed** |
| `4` | Your JSON, not a Baton fault. Fix every `details.violations` entry (each carries a pointer) and resubmit changed content |
| `6` | Report; the draft and summary are kept, so a retry resumes |
| `7` | Nothing published to take back (`unpublish`). Report and stop |
