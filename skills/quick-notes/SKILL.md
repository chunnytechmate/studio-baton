---
name: quick-notes
description: "Push a note to the studio's notes page. Use when asked to jot something down, save a note, or put today's notes somewhere. Markdown is converted by Baton, not by you."
---

# Quick notes

## Triggers

- "note that ...", "save this to my notes"
- "push today's notes"

Not a trigger: a lesson summary. That is `lesson-summarizer`.

## Commands

```bash
baton notes preview --text "<markdown>" --json    # what it becomes
baton notes push --text "<markdown>" --json
baton notes push --file <path> --title "..." --json
```

## Rules

**Write Markdown. Never build blocks.** Headings, bullets, `- [ ]` tasks,
numbered lists, quotes, fenced code, and `---` are all understood. Anything else
becomes a paragraph, so nothing is lost.

**Keep tabs and diagrams in a fenced code block.** Inside a fence, spacing and
blank lines are preserved; outside it they are not.

**Use `preview` when the note is long or the structure matters**, and show the
user the block counts before pushing.

**Do not title the note yourself unless the user gave one.** Baton takes the
first heading, or the date.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report the page link |
| `1` | Usually a missing parent page id: the message names the variable |
| `2` | Run `baton doctor` |
| `6` | The store failed; nothing was created. Re-running is safe |
