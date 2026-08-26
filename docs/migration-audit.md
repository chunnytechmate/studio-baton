# Migration audit: OpenClaw skills → Studio Baton

Studio Baton replaced a set of hand-written OpenClaw skills one domain at a
time. Each rewrite was a chance to drop a behaviour that the old script had
learned the hard way and that nothing in the new code records the absence of.
One such gap reached production: `baton video` fed phone clips straight into
ffmpeg's `concat` filter without the per-clip normalisation the old
`video_tools.py` did in a separate pass, so any lesson filmed partly in
landscape and partly in portrait failed to encode. It had been that way since
the rewrite; it surfaced only when a learner happened to film that way.

This document is the standing audit that looks for the rest of them. It is a
working file: claim a lane before auditing it, record what you checked, and
leave the verdict where the next person can find it.

**Started:** 2026-08-27 · **Baton version at start:** 0.2.8

---

## How to audit a lane

1. **Claim it** — put your name and the date in the Owner column below, commit,
   and only then start. Two people auditing the same lane is the waste this
   file exists to prevent.
2. **Read the old skill first, not the new code.** The question is "what did
   this do", not "does the new code look reasonable". A capability nobody
   remembers is exactly the one that gets dropped.
   Old skills live in `~/.openclaw/workspace/skills/<name>/` (and
   `archived-skills/` for retired ones). Read `SKILL.md`, then every script —
   including the `notes/`, `KNOWN_ISSUES.md`, and `TROUBLESHOOT*.md` files,
   which is where the hard-won behaviours are written down.
3. **List capabilities, then check each one against Baton.** Not just the happy
   path: retries, gates, orderings, fallbacks, error text, idempotency,
   cleanup, what happens on partial failure.
4. **Prove it, don't infer it.** Reproduce the old behaviour and the new one.
   For anything ffmpeg- or API-shaped, run it in the `openclaw-gateway`
   container, not on the host — versions differ and so does the wording of
   errors.
5. **Record the verdict below** — including "checked, no gap", which is worth
   as much as a finding and stops the lane being re-audited.

### What counts as a finding

A behaviour the old skill had, that a studio depends on, that Baton does not
have. Three flavours, all worth reporting:

- **Dropped capability** — the old code did it, the new code does not.
- **Silent degradation** — the new code appears to do it but does not (a
  config value with no branch behind it, a docstring that promises more than
  the chain delivers).
- **Lost hard-won detail** — a retry, an ordering, an error message, or a gate
  that exists because of a specific incident, and whose absence only shows up
  during the next one.

Not a finding: Baton doing something differently but no worse, or a
deliberately-deferred decision that is written down as deferred.

---

## Lanes

| # | Domain | Old skill | Baton counterpart | Status | Owner |
|---|--------|-----------|-------------------|--------|-------|
| 1 | Video encode | `video_pipeline/core/video_tools.py` | `adapters/media/ffmpeg.py` | ✅ done — 1 finding, fixed | Claude 2026-08-27 |
| 2 | Video: Drive + YouTube + orchestration | `video_pipeline/core/{drive_manager,drive_cleanup,youtube_uploader,pipeline_orchestrator}.py` | `adapters/media/google.py`, `pipelines/video.py` | 🟡 partial — spot-checked, see notes | Claude 2026-08-27 |
| 3 | Calendar | `google-calendar/scripts/{scal,gcal}.py` | `pipelines/schedule.py`, `adapters/cal/google.py`, `cli/cmd_calendar.py` | ✅ done — 1 finding, fixed | Claude 2026-08-27 |
| 4 | Lesson summary | `music-class-summarizer/` | `pipelines/{staging,publish}.py`, `cli/cmd_lesson.py`, `render/` | 🟡 partial — 2 findings (1 fixed, 1 open), see notes | Claude 2026-08-27 |
| 5 | Send to LINE | `send-lesson-line/` | `pipelines/send.py`, `cli/cmd_send.py`, `adapters/chat/` | 🟡 in progress | Codex 2026-08-27 |
| 6 | Send recording / YouTube link | `send-recording-line/`, `send-youtube-line/` | `baton send recording`; **no `send youtube` yet** | ⬜ unclaimed | — |
| 7 | Course archive + clear | `notion-clear-student/` | `cli/cmd_course.py`, `domain/archive.py` | ✅ spot-checked — no gap | Claude 2026-08-27 |
| 8 | Student records | `student-management/`, `student-lookup/` | `cli/cmd_learner.py`, `adapters/db/` | ⬜ unclaimed | — |
| 9 | Prep report / notes | `student-management/scripts/prep_report.py`, `song_manager.py` | `cli/cmd_prep.py`, `cmd_notes.py`, `domain/prep.py` | ⬜ unclaimed | — |
| 10 | Retired skills | `archived-skills/{get-latest-lesson,list-completed-lessons,list-inprogress-lessons,student-works}` | `baton learner`, `calendar in-progress` | ⬜ unclaimed | — |

Legend: ✅ done · 🟡 partial (notes say what is left) · ⬜ unclaimed

---

## Findings

### F1 — `concat` fed unnormalised clips ✅ fixed in 0.2.8

**Lane 1.** `_args()` built `concat` first and applied the scale chain after
it, and the live profile (`auto`) had no branch at all, so nothing normalised
anything. `concat` the filter tolerates mixed codecs but still requires one
frame size and SAR across segments. A session filmed partly in landscape and
partly in portrait — ffprobe reports both as 1920x1080; the display matrix
makes two of them decode to 1080x1920 — failed to configure the filter.

The old `video_tools.combine_clips()` did a separate `normalize_orientation()`
pass per clip first. That pass was dropped whole in the rewrite, taking
`setsar=1`, frame-rate normalisation, audio reconciliation, HDR tone-mapping,
and the iPhone edit-list flags with it.

Fixed: each input gets its own chain before the concat; `auto` now means
"reconcile only when the clips disagree", so a uniform session encodes exactly
as before. `tone_map_hdr` and `fps` are new config keys.

### F2 — the reported ffmpeg line was the last, not the first ✅ fixed in 0.2.8

**Lane 1.** `_one_line()` took the final stderr line. ffmpeg's diagnosis is the
*first* line; the rest is the pipeline unwinding. The job record for F1 read
`ffmpeg failed: Error while processing the decoded data for stream #2:1`,
which names neither a cause nor a clip. Fixed, with the real stderr now
carried in the error's `details`.

### F3 — Google Calendar had no retry and leaked vendor exceptions ✅ fixed

**Lane 3.** `create`, `list_between`, and `health` called `.execute()` raw.
Two consequences:

- No retry. The old `gcal.py` had `gcal_api_call_with_retry` — exponential
  backoff over 429/403/5xx. Every other Baton adapter retries (Notion and LINE
  through `core.retry.http_request`, Drive and YouTube through `num_retries`);
  calendar was the only one that did not.
- A `googleapiclient.HttpError` from `create` is not a `BatonError`, so it
  escaped the CLI's handler as a traceback rather than an exit code — the one
  thing `doctor` explicitly guards against, and unguarded everywhere else.

This lands hardest on `book`: by the time `create` runs, the session document
is already marked in progress. The ordering in `pipelines/schedule.py` exists
so the two records cannot disagree, and a transient 429 here was the one case
that could still split them.

Fixed: all calls go through `_calendar_call`, which retries transient faults
with the shared backoff and maps everything else to `UpstreamError`. Attempts
configurable via `calendar.google.attempts`.

### F4 — published summaries lost their AI disclosure ✅ fixed

**Lane 4.** Every summary the old pipeline published carried a dated
disclosure built by `music-class-summarizer/scripts/ai_footer.py` — who wrote
it, when, and that it came from an assistant. `render/summary.py` has no
footer, so every summary published through Baton since 2026-08-23 has gone to
parents without it.

Baton knew the footer existed: `docs.summary_footer` is a regex in
`domain/prep.py` used to *strip* it out of previous summaries when gathering
context. It strips a thing it never writes.

The YouTube description renderer (`render/youtube.py`) does append its own
disclosure, which is how this stayed invisible — the videos carried it, the
pages did not.

Fixed: `summary.footer` config (lines, date format, era, month names), a new
`domain/footer.py`, rendered into both `to_blocks` and `to_markdown`. Empty by
default so no other studio gets a disclaimer it did not ask for. Month names
are configured rather than left to `%B`, which renders English wherever the
studio's locale is not installed.

### F5 — a failed YouTube description update has no retry path 🔴 open

**Lane 4.** `lesson publish` updates the video description as a best-effort
step after the document is written. If it fails, the failure is recorded on
the draft and reported — but a re-run hits the "already published" gate and
returns OK without retrying, and `--force` would re-append the whole summary
to the page.

The old pipeline tracked `push_state` per service: `push --all` skipped Notion
when it was already `ok` and retried YouTube up to three times. Baton's
per-target `record_target` holds the same information but nothing acts on it
for the YouTube target.

Suggested shape: let `publish` resume a `youtube: failed` target the way it
already resumes an unfinished `docs` target (`_finish_session(resumed=True)`),
without re-appending blocks.

---

## Notes on partially-audited lanes

### Lane 2 — video: Drive, YouTube, orchestration

Spot-checked and found equivalent, not exhaustively audited:

- Resumable chunked upload with `num_retries=3`, title truncated to YouTube's
  100 characters, `selfDeclaredMadeForKids` — parity with `youtube_uploader.py`.
- Ownership check before `update_description` — carried over, and the
  docstring records the 2026-08-09 incident it came from.
- Deferred trashing (sources are only moved after everything else for that
  learner succeeded) — parity with `_process_student_group`.
- The `getaddrinfo_ipv4_fixed` monkey-patch in both old scripts has no Baton
  equivalent, and does not need one: IPv6 is disabled inside the container
  (`/proc/sys/net/ipv6/conf/all/disable_ipv6` is 1), which is what the patch
  was working around.

**Still to check:** `pipeline_orchestrator.py` against `pipelines/video.py`
step by step; `drive_cleanup.py`'s rules for what counts as a generated
artifact versus a source clip; `preflight_drive.py`; `check_week_state.py` and
`check_trash_state.py`; the watcher loop's behaviour when a download partially
fails.

### Lane 4 — lesson summary

Checked and equivalent or better:

- Publish ordering (append, then delete) and the preserve policy — mechanism
  where the old system had a standing warning in prose.
- Per-target state and the published record, versus the old `push_state`
  (except F5).
- `tone` and `has_instrument` carried through the models and both DB adapters.
- `--dry-run` on publish, via `SummaryPublisher.plan`.
- Theory callouts resolved from the studio's own store, not written by the model.

**Still to check:** `services/blocks.py` block-building against `render/`
(especially code-fence language handling and any block type the old one emitted
that the new schema has no field for); `staging.py` versus
`pipelines/staging.py` for the atomic-write and `.bak` behaviour;
`edit_summary.py`'s editing surface versus `lesson ingest`; the
`line_summaries.json` cache format that the reconcile cron still reads.

### Lane 3 — calendar, checked and no gap

Recorded so nobody re-checks them:

- Name resolution is a stricter gate in Baton (`domain/resolve.py`): exact,
  then a single alias hop, and *never* an automatic partial match however few
  candidates there are. `scal.py` had the same intent.
- `parse_schedule` handles `17.00`, `17:00`, and `17`; free markers still bound
  the previous slot rather than extending it; a malformed line raises instead
  of being silently skipped.
- Document-before-event ordering on book, and the reverse on cancel.
- A duplicate booking of the same session on the same day is refused — the old
  `_find_duplicate_event` matched on title alone.
- Cancels outside `rollback_window_days` are refused. The old skill left this
  to the agent's discipline in `SKILL.md`.
- Instrument emoji, Thai date shorthand (`วน`, `พน`, `มร`, and the long forms)
  are configured in the studio profile.

**Known non-gap, worth writing down:** `baton calendar` has no equivalent of
`gcal.py add` for a non-student event (a run, a meeting). That is not a
regression — `AGENTS.md` still routes general calendar work to `gcal.py` — but
it is the one thing blocking the calendar lane from being switched over to
Baton entirely.

---

## Verifying a fix

Full suite, lints, and types:

```bash
cd ~/studio-baton
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/baton
```

Deploying to the running gateway — **both installs, or the fix silently does
not apply**:

```bash
uv build --wheel
docker cp dist/studio_baton-<version>-py3-none-any.whl openclaw-gateway:/tmp/
docker exec -u root openclaw-gateway python3 -m pip install \
  --no-deps --force-reinstall --break-system-packages /tmp/studio_baton-<version>-py3-none-any.whl
docker exec openclaw-gateway /home/node/.openclaw/baton-venv/bin/python3 -m pip install \
  --no-deps --force-reinstall /tmp/studio_baton-<version>-py3-none-any.whl
docker exec openclaw-gateway baton --version   # expect the new version
```

> ⚠️ `docker-compose.yml` pins `studio-baton[google]==0.2.7` from PyPI. Any
> image rebuild reverts every hand-installed wheel. Publish to PyPI or repoint
> that build arg before rebuilding.
