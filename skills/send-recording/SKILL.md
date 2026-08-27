---
name: send-recording
description: "Send a learner's recorded work — its Drive and YouTube links — to a contact. Use when asked to share a recording, send a video of someone's playing, or send a cover/practice track. Never guesses which recording: lists them, waits for a number."
---

# Send recording

A recording lives in the database as one work row with up to two homes: YouTube
and Drive. A side that is empty is simply not sent. A work with neither link is
refused outright — an announcement of a recording without the recording is
worse than silence.

## Triggers

- "ส่งผลงานของ X", "send X's recording", "share the latest cover"
- Not this skill: "ส่งสรุปการเรียน" / "send X's summary" belongs to send-lesson
  — a session summary is written prose, a recording is stored links.

## Two invocations, always

Which recording to send is a person's choice; the date order only suggests.
Never assume the newest is the wanted one, and nothing is remembered between
calls — if the base may have changed, re-list first.

```bash
baton send contacts --json                          # who can receive
baton send recording "<name>" --json                # round 1: the list
# round 2's number comes from that list; shown with 1:
baton send recording "<name>" --to <contact> --dry-run --pick 1 --json
baton send recording "<name>" --to <contact> --pick 1 --json
```

## Round 1 — show the list, ask for a number

Without `--pick` the command exits `3` carrying `details.candidates`, newest
first (`n`, title, type, performed_date, both links). It has sent nothing. Relay
that list as numbered lines and ask which recording the parent means.

## Round 2 — deliver exactly the picked row

`--pick N` means the Nth line of the list just shown — never "the latest".
Run `--dry-run` once when sending on behalf of a contact for the first time
that day; it composes the exact message without pushing anything.

## Rules

**One pick, one work.** If a parent asks for several works, that is several
rounds of picks — do not improvise a combined message.

**Exit 5 means nothing was sent and must not be forced.** A work with no links
at all fails closed. Report `details.missing` and stop: record the links first,
there is no override flag, and you must not go looking for one.

**If a command fails, do not send the links by hand.** No API calls, no other
tools. Report the failure and stop.

**"Already sent" means it went out.** Baton keeps a receipt of each delivery, so
picking the same recording for the same contact twice is refused with exit `5`
and the time of the first send. Report that; it is the answer, not a problem to
work around. `--again` is for a person who has confirmed nothing arrived.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Reported what was delivered, and its warnings |
| `1` | `--pick` matched no row — re-list and ask again |
| `3` | Show `details.candidates`; ask which recording or full name |
| `5` | **Nothing was sent.** Work has no links — or it was already sent, and `details.already_sent.sent_at` says when. Do not retry |
| `6` | LINE failed; report, and say nothing went out |
| `8` | Another send is in flight. Wait, then re-run |
| `9` | Baton crashed. Report the traceback; do not retry |
