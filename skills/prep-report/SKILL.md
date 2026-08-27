---
name: prep-report
description: "Brief the teacher before a day's lessons: every booked learner's last session — content, homework, next goal — or nothing. Use when asked to prepare teaching, brief today's lessons, or summarise where each learner stands."
---

# Prep report

Walking into a lesson cold is the failure this exists for. `baton prep` reads
every booked learner's latest finished session and prints one briefing; your
whole job is to hand that briefing over **unchanged**.

## Triggers

- "เตรียมการสอนวันนี้", "brief คาบวันนี้", "prep report"
- "น้องX เรียนถึงไหนแล้ว สรุปหน่อย" (single learner — pass `--learner`)
- "what should I cover today", "prepare today's lessons"

## The command

```bash
baton prep                 # the report itself (date defaults to today)
baton prep --date 2026-08-22
baton prep --learner "น้องสมพร" --learner "น้องวีระ"
baton prep --json          # machine output; the verbatim report is under "report"
```

Who appears: every learner booked on the day (calendar titles Baton itself
writes — `Name (Week N)`), or exactly the `--learner` names given.

## Rules

**Relay verbatim.** The report Baton prints *is* the briefing. Copy it through
as-is — every line, every link. Do not re-compose, shorten, or "clean up" the
text: an agent rewriting the report is exactly how the Notion links went
missing before this command existed. In `--json` mode the same text sits
under `report` — relay that string, not your own summary of the fields.
Adding a greeting around it is fine; editing inside it is not.

**Blocked means blocked.** A learner missing any required field (week, date,
titles, notion_link, overview, content, homework) is listed under `BLOCKED`
with what they lack. Report the block, do not reconstruct their section from
memory or guess — the page is incomplete and the teacher must know that.

**Exit `5` means nobody passed.** There is no report. Show the blocked list
from `details` (or re-run without `--json`) and stop.

**One learner, one command.** `--learner` is repeatable; do not run the
command once per learner when asked about several — one report keeps the
briefing in teaching order.

**Next goal is a warning, not a blocker.** `(none stated)` in the report
means the page had no next-goal section. Mention it; don't treat it as an
error.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Relay the printed report verbatim (blocked learners, if any, are in it) |
| `1` | Read the message; the invocation was wrong |
| `2` | Run `baton doctor`, report what it says, stop |
| `3` | Show `details.candidates`, ask, re-run with the exact name |
| `5` | Nobody passed the gate. Report the blocked list and stop |
| `6` | Report; the calendar or document service is down, re-running later is safe |
