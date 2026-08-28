---
name: studio-songs
description: "List, search, add, edit, and remove pieces in the shared catalogue `learner assign` points learners at. Use when asked to add a new song/piece, update its links, look up who is playing something, or delete one from the catalogue."
---

# Studio songs

The piece catalogue is shared: no one learner owns a row in it, and
`learner assign` is what points a learner at one. Removing a piece a learner
still points at is refused, not overridden.

## Triggers

- "add a new song", "add this piece with a practice track"
- "who is playing Blackbird", "what pieces do we have"
- "update the sheet link for X", "delete this piece"

## Commands

```bash
baton song list --json
baton song search "<word>" --json
baton song show <id> --json
baton song add "<title>" --json                       # links are optional
baton song add "<title>" --practice-track <url> --sheet-link <url> --json
baton song update <id> --title "<new title>" --json    # only the flags given change
baton song update <id> --sheet-link <url> --json
baton song update <id> --sheet-link "" --json          # clears the link
baton song remove <id> --json
baton song remove <id> --dry-run --json                # preview first
```

## Rules

**`song update` only touches the fields given.** A flag left out leaves that
field alone. Passing an empty value (`--sheet-link ""`) clears it — that is
different from not passing the flag at all, so never pass an empty value
unless the user actually asked to remove a link.

**Unknown ids are refused, never silently accepted.** Updating or removing a
piece that does not exist is exit `1`, not a quiet success — check `song show`
first if unsure an id is right.

**`song remove` is exit `5` while any learner is assigned.** The message
names them. Reassign or unassign with `baton learner assign` first — there is
no override.

**Use `--dry-run` before a remove the user has not explicitly confirmed.**
Show what would be removed, and who (if anyone) is holding it, before running
it for real.

**`song add` has no duplicate check.** Run `song search "<title>"` first if a
duplicate would matter — Baton will not catch it for you here.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report the result |
| `1` | Unknown id, a blank title, or an update with nothing to change — the message says which |
| `2` | Run `baton doctor`, report what it says, stop |
| `5` | A learner is still assigned. Report who, and stop — no override |
| `6` | Report; the service is down, re-running later is safe |
