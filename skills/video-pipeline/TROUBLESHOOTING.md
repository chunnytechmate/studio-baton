# Video pipeline troubleshooting

Symptoms in the order they actually come up. Every answer here ends in
`baton video resume --detach --json`, because the pipeline records each step
atomically and skips the ones that already succeeded: deleting state to "start
clean" is how a second copy of a child's lesson gets published.

## The job says `running` but the log is empty

Read the existing job. Do not start another one.

```bash
baton job status <id> --json
baton job logs <id> --tail 100
baton video status --json
```

An empty log during encoding is normal: ffmpeg runs at `-loglevel error`, and
the `combined` step is recorded only after it finishes. A job that is `running`
with a live heartbeat, and an ffmpeg process using CPU, is working.

Do not delete `.baton-encode-*`, a source clip, or the job state while the job
is running. Those are the encoder's working files and the only record of what
has already been done.

## Your wait timed out

`baton job wait <id> --timeout N` exits `8` when *the wait* runs out. The
detached job is unaffected and still running: check `baton job status` with
the same id, and do not start a second run.

Keep `--timeout` below whatever your harness allows one command to take
(Claude Code's default is two minutes). A wait that gets killed tells you
nothing; exit `143` is your wait being killed, never the job failing.

## The job is `failed`

1. `baton job logs <id> --tail 200`
2. `baton video status --json`, which learner, and which step
3. Fix the outside cause: credentials, quota, network, disk space
4. Confirm no other video job is `running` (`baton job list --json`)
5. `baton video resume --detach --json`

## The job is `orphaned` or `stopped`

- `orphaned`: the supervisor died without recording an outcome. Liveness is
  checked, not assumed, so this is a real answer rather than a job that hangs
  forever in `running`. Read the state, then resume.
- `stopped`: someone or something stopped it (`baton job stop`, a container
  restart). Same treatment: read the state, then resume.

## ffmpeg failed

Read the real error from the job log before changing anything. The usual causes
are a corrupt input clip, a full disk, or the watchdog timing out, not the
encoder choice. The profile encodes with `libx264` on purpose; switching to a
hardware encoder to get past one bad clip changes every recording the studio
publishes.

## YouTube, Drive, or the document store failed

- Report the error code from the job log. Never paste a token or credential.
- If the upload already succeeded, the state holds `video_id` and `video_url`,
  and a resume skips the upload rather than repeating it. Check those two
  fields before doing anything else.
- Source clips are trashed last, after every other step succeeded. Until then
  they are the only copy of the lesson.
- A `done` job whose entry is missing `uploaded`, `doc_linked`, or
  `source_trashed` is incomplete. Report it as incomplete and investigate; do
  not call it finished.

### `youtube request failed: RefreshError`

The refresh token was rejected. Read the exception (without printing the
token) and check whether Google answered `invalid_scope`: that means the
scopes being requested no longer match the ones the stored authorized-user
token was granted, which re-authorising fixes and retrying does not.
