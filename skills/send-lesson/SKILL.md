---
name: send-lesson
description: "Send a published lesson summary to a contact. Use when asked to send the summary, message a parent, or send the day's lessons. Refuses to send anything incomplete."
---

# Send lesson

Sends the message that was published — never one re-derived at send time.

## Triggers

- "send X's summary", "message the parents about X"
- "send today's lessons to <contact>"

## Commands

```bash
baton send contacts --json                              # who can receive
baton send lesson "<name>" --to <contact> --dry-run --json
baton send lesson "<name>" --to <contact> --json

# Several learners: ONE invocation, never a loop
baton send batch --to <contact> \
  --learner "<name>" --learner "<name>" --learner "<name>" --json
```

## Rules

**Run `--dry-run` first when sending for the first time that day.** It runs the
same gate and shows the exact message, without sending.

**Several learners go through `send batch`, in one command.** Do not loop over
`send lesson`. A loop loses track of which ones went, which is the failure the
batch report exists to prevent.

**Exit 5 means the message was NOT sent, and must not be forced.**
`details.missing` lists each gap with how to fix it. Report those to the user
and stop. There is no override flag, and there is no way to send an incomplete
message — do not look for one, and never send the message another way.

**If a command fails, do not send the message by hand.** No API calls, no other
tools. Report the failure and stop.

**"Already sent" means it went out. Say so and stop.** Baton keeps a receipt of
every delivery, so a repeat of the same learner's same session is refused with
exit `5` and a message saying when the first one was sent. This is what catches
the message your last command *did* deliver before it was killed. `--again`
overrides it and belongs to a person who has confirmed the family never
received anything — never use it to get past the refusal on your own.

**Exit 8 means another run is sending right now.** Not necessarily yours: two
agents on one profile see each other here. Wait and re-run; do not stop the
other one.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report what was sent, and any `warnings` |
| `1` | Nothing published for that learner — publish first |
| `3` | Show `details.candidates`, ask which contact or learner |
| `5` | **Nothing was sent.** Report `details.missing` — or, when the message says *already sent*, report that it went out at `details.already_sent.sent_at`. Do not retry either way |
| `6` | The platform failed. Report; the message did not go |
| `8` | Another send holds the lock. Wait, then re-run |
| `9` | Baton crashed. Report the traceback; do not retry |
| `143` | Killed by the harness mid-send. **Do not assume it failed** — re-run; the receipt will tell you if it already went |

After a batch, always report the totals *and* name everyone in `blocked`.
