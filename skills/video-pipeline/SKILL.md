---
name: video-pipeline
description: "Collect lesson recordings, combine them, publish, and link them to the session page. Long-running. Use when asked to process videos, upload recordings, or run the video pipeline."
---

# Video pipeline

Runs for tens of minutes. Start it detached and report the job id; do not sit
and wait for it.

## Triggers

- "process the videos", "upload the recordings"
- "run the video pipeline", "did the videos finish"

## Commands

```bash
baton video run --dry-run --json        # what is waiting, changes nothing
baton video run --detach --json         # start it; returns a job id at once
baton job wait <id> --timeout 90 --json
baton job logs <id> --tail 50
baton video status --json               # per-learner progress; every job has
                                        # learner_name, and error is null (not
                                        # "") when there is none
baton video resume --detach --json      # continue whatever did not finish
```

## Rules

**Always `--detach`.** A foreground run outlives no session. Report the job id
and the two follow-up commands to the user.

**`job wait` exits with the job's own exit code.** Exit `8` means it is still
running — that is not a failure, report it and offer to keep waiting.

**Keep `--timeout` under your own harness's limit.** Claude Code kills a shell
command at two minutes by default; a `--timeout 600` never returns its exit 8,
it gets killed, and a killed wait tells you nothing about the job. Wait in short
turns instead — 90 seconds, report, wait again — or just report the job id and
check `baton job status` later. The job outlives every one of these calls.

**Re-running is safe and is the correct response to most failures.** A finished
upload is never repeated, and source clips are only discarded once everything
else succeeded. Use `baton video resume`.

**A new week's clips start a new job by themselves.** When a folder whose job
is already done receives clips the job has never seen, `video run` archives
the done job and starts the next session's job — `video forget` is no longer
part of the weekly loop. If no next session exists yet, that learner fails
with a remedy naming it; create the session, then re-run.

**`video resume` never collects, but never stays quiet either.** It continues
unfinished jobs and re-trashes source clips a done job's record claims were
already moved. When clips are waiting that only `video run` may collect, the
result says so (`waiting_clips`) — report that and suggest `video run`.

**A finished job survives a document-store outage.** The link step is
re-checked against the live page on every pass, but when the step is already
recorded and the page cannot be reached, the record stands — so a `done` job
does not read as `failed` because Notion was briefly down.

**Never use `baton video forget`** unless the user explicitly asks and
understands it: if the upload already happened, starting over publishes a
second copy.

**A skipped learner is not a failure.** It means no learner is named exactly
like that source folder. Report the folder name and ask which learner it is —
do not guess, and do not rename anything yourself.

## Exit codes

| Code | Do this |
| --- | --- |
| `0` | Report per-learner results |
| `2` | Run `baton doctor`; credentials or ffmpeg are missing |
| `6` | Some learners failed. Report them, suggest `baton video resume` |
| `7` | A job died without recording an outcome. Report; re-running is safe |
| `8` | Still running, or another video run holds the lock. Report the job id |
| `9` | Baton crashed. Report the traceback; do not retry |
| `143` | Your wait was killed by the harness, not the job. The job is unaffected — check `baton job status` |
