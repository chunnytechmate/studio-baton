---
name: student-lookup
description: "Look up learners, their sessions, pieces, and recorded work. Use when asked who is learning what, which session someone is on, what is still in progress, or to record a performance."
---

# Student lookup

Every command takes `--json` and prints one document. Read the exit code first.

## Triggers

- "who is on session N", "what is X working on"
- "which sessions are in progress", "what do I teach today"
- "show me X's recordings", "add a performance for X"
- "list the pieces", "assign X to piece N"

## Commands

| Ask | Command |
| --- | --- |
| Everyone | `baton learner list --json` |
| One learner in full | `baton learner show "<name>" --json` |
| All their sessions | `baton learner sessions "<name>" --json` |
| **The latest session that happened** | `baton learner latest "<name>" --json` |
| The next free session | `baton learner next "<name>" --json` |
| Who still owes a summary | `baton learner in-progress --json` |
| Their recordings | `baton learner works "<name>" --json` |
| Record a performance | `baton learner add-work "<name>" --title "..." --type cover --json` |
| The piece catalogue | `baton learner pieces --json` |
| Assign a piece | `baton learner assign "<name>" --piece <id> --json` |
| Assign and repair published piece sections | `baton learner assign "<name>" --piece <id> --update-published --dry-run --json` |

## Rules

**"Latest" is `learner latest`, never the highest session number.** Sessions get
skipped — illness, cancellations, pages made in advance — so a high number
proves nothing about what happened. The command already applies this.

**`learner latest` also returns the page as `sections`** — overview, content,
focus, practice goals, next goal, homework — so "what did we do last time" needs
no second command. `sections_unreadable` means the page could not be read, not
that it was empty. For a whole day of lessons at once, use `baton prep`.

**A name that is not exact stops the work.** Exit `3` carries
`details.candidates`. Show them to the user and wait for an answer. Do not pick
the only candidate, even when there is only one.

**Writes take `--dry-run`.** Use it when the user's intent is ambiguous, show
the result, and confirm before running it for real.

When changing a piece after a lesson was published, include
`--update-published`. First run it with `--dry-run` and report the exact pages
in `published_updates.pages`; the real run replaces only Baton's rendered
piece heading and resources from the old assignment. It does not rewrite the
lesson summary, recording, or other preserved callouts.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report the result |
| `2` | Run `baton doctor`, report what it says, stop |
| `3` | Show `details.candidates`, ask, re-run with the exact name |
| `6` | Report; the service is down, re-running later is safe |
