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

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report what was sent, and any `warnings` |
| `1` | Nothing published for that learner — publish first |
| `3` | Show `details.candidates`, ask which contact or learner |
| `5` | **Nothing was sent.** Report `details.missing`. Do not retry |
| `6` | The platform failed. Report; the message did not go |

After a batch, always report the totals *and* name everyone in `blocked`.
