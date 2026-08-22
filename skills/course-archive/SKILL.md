---
name: course-archive
description: "File a finished course before emptying it for the next one. Use when asked to clear, reset, or start over a learner's course, or to archive the course that just ended."
---

# Course archive

A course ends and the same pages are reused for the next one. File a copy
first, prove it is complete, and only then empty them.

Baton does everything except the copy. **You make the copy** with the
harness's duplicate tool — the documents API cannot reproduce a table's
layout, and a rebuilt table looks like an archive without being one.

## Triggers

- "clear X's course", "reset X", "start a new course for X"
- "archive the course that just finished"
- "empty X's pages for next term"

## The order

| Step | What runs |
| --- | --- |
| 1 | `baton course plan "<name>" --json` |
| 2 | Show the plan. Wait for the person to confirm. |
| 3 | Duplicate the page with the harness's duplicate tool |
| 4 | `baton course verify "<name>" --page <copy-id> --json` |
| 5 | `baton course clear "<name>" --json` |

### 1. Plan

```bash
baton course plan "<name>" --json
baton course plan "<name>" --label "<piece>" --json
```

Read from the payload: `course.page_id` (what to copy), `archive.title` (what
to call the copy), `archive.destination_id` and `archive.needs_move` (where it
belongs), `rows` (how many rows the copy must end up with).

### 2. Confirm

Show the name the copy will take and how many sessions it holds. This step
destroys a course's contents afterwards — do not skip it, and do not accept a
vague yes. If `renames_live_page` is true, say so: the live page already
carries that name and will need renaming for the new course.

### 3. Copy

Duplicate `course.page_id` with the harness's duplicate tool, then:

- **Wait for it to finish.** Duplication is asynchronous. Re-read the copy
  until its table holds `rows` rows. A copy checked too early looks empty.
- **Move it** into `archive.destination_id` when `archive.needs_move` is true.
  When it is false the copy already landed where finished courses are kept.
- **Rename it** to `archive.title`.

Keep the id the duplicate tool returned. It is not `course.page_id`, and for a
studio that names its live page after the span it is teaching the two carry the
same title — verify refuses that mistake, which is the only thing standing
between a clear and the course it was meant to preserve.

### 4. Verify

```bash
baton course verify "<name>" --page <copy-id> --json
```

Exit `0` means the copy's name, filing, and every row match the live course.
**Exit `5` means stop.** `problems` says what is wrong. Fix the copy and verify
again — never clear on a failed verify.

### 5. Clear

```bash
baton course clear "<name>" --json
baton course clear "<name>" --dry-run --json
```

Empties every session page and its properties. The rows stay, keeping their
numbers, so the next course reuses them.

Clear enforces the archive itself: it re-reads the filed copy and refuses
(exit `5`) unless it is complete — a copy that passed verify yesterday but
was trashed today no longer protects anything. If the plan used `--label`,
pass the same `--label` to clear, or it will look for a copy filed under the
wrong name.

## Rules

**Only when asked.** These commands are destructive and are never a repair
step. Do not reach for `clear` to undo a bad summary — that is what the lesson
tools are for.

**Clear checks.** It refuses (exit `5`) unless a complete copy of the course
is filed — re-read at clear time, not remembered from step 4. There is no
flag that bypasses this; the only way through is a complete archive.

**One session is not a course.** `--session N` empties a single page and
skips the archive rule — a partial clear is a mid-course tool with no
finished course to file. It is the one deliberate exception; `--dry-run` is
the other (it empties nothing).

**A name that is not exact stops the work.** Exit `3` carries
`details.candidates`. Show them and wait; never pick one.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report the result and move to the next step |
| `1` | Read the message; the invocation was wrong |
| `2` | Run `baton doctor`, report what it says, stop |
| `3` | Show `details.candidates`, ask, re-run with the exact name |
| `5` | Stop. The copy is not usable, the course is already filed, or `clear` found no complete archive to protect it. Report `problems` |
| `6` | Report; the service is down, re-running later is safe |
