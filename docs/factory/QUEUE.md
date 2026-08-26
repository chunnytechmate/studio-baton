# Factory queue — audit snapshot

Snapshot written by triage. **The live queue is GitHub issue labels plus the latest
`factory-handoff:v1` comment.** This file is for humans and audit; an unmerged snapshot
must never block or override a live label.

- snapshot taken: 2026-08-26 (run `2026-08-26T001142Z-triage-queue`, UTC `2026-08-26T00:11Z`)
- base commit: `8409b5e` (origin/main — merge of PR #53, the FQ-52 evidence-boundary correction)
- coverage this run: **0 untriaged issues** — every open issue carries exactly one state
  label. 14 open issues were updated since the last triage run
  (`2026-08-24T000806Z-triage-4`): 11 re-triaged — all `wait-to-implement` with their
  named blockers reconfirmed live and unchanged — and 3 excluded as claimed or
  human-owned (#41, #10, #32; see "Not covered")
- cap 20 not reached — **0 issues skipped by the cap**
- entries FQ-4 through FQ-24 below were last triaged 2026-08-24 and have not been
  updated since; their labels were read live this run and are unchanged

## FQ-6: M3: TOCTOU ใน JobRunner.get — heartbeat stat race กับ prune → FileNotFoundError
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/6
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: `hb_path.exists()` then `.stat()` at `src/baton/core/jobs.py:337`, no guard)
- files_expected: src/baton/core/jobs.py, tests/test_jobs_toctou.py (new)
- load_bearing: false
- gate_level: full
- done_when: new test simulates the heartbeat file vanishing between exists() and stat() (monkeypatched stat raising FileNotFoundError) and asserts `JobRunner.get` returns per contract (None or a contract error) with no raw FileNotFoundError escaping
- confidence: medium
- notes: race itself is hard to hit in a test — monkeypatch the second filesystem call; the fix is a guard, not a redesign

## FQ-7: M4: prune นับ "removed" ทะลุแม้ rmdir ล้ม
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/7
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit in review/findings.md; `removed += 1` outside the `suppress(OSError)` re-verified at `src/baton/core/jobs.py:481-486`)
- files_expected: src/baton/core/jobs.py, tests/test_jobs_prune_count.py (new)
- load_bearing: false
- gate_level: full
- done_when: new test prunes a terminal job dir (older than cutoff) containing a subdirectory that blocks rmdir and asserts that dir is NOT counted in the returned removed count; a cleanly removable dir still is
- confidence: high
- notes: fix direction mechanical — count only successful removals (and surface the failure)

## FQ-10: M12: notes: --text "" แจ้ง error ผิดเรื่อง; title heuristic จับบรรทัด fence/ตารางเป็นชื่อ
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/10
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit; `bool(text) == bool(source)` at `cmd_notes.py:81-84` and `_title_for` at `:95-111` re-verified at HEAD)
- files_expected: src/baton/cli/cmd_notes.py, tests/test_notes_text_edge.py (new)
- load_bearing: false
- gate_level: full
- done_when: new tests prove (1) `notes add --text ""` without --file raises an error naming the empty note — not "Pass exactly one of --text or --file" — and (2) a note whose first non-blank line is a fence or table row does not get that line as its title
- confidence: high
- notes: single-file CLI fix; both failure modes still present at HEAD

## FQ-11: M13: init --force --sample-data รันซ้ำสองครั้ง → sqlite IntegrityError traceback
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/11
- last_triaged: 2026-08-24
- repro: confirmed (run evidence in review/findings.md; unconditional `executescript` of `seed_example.sql` with no sqlite error handling re-verified at `cmd_init.py:226-232`)
- files_expected: src/baton/cli/cmd_init.py, src/baton/migrations/seed_example.sql, tests/test_init_sample_idempotent.py (new)
- load_bearing: false
- gate_level: full
- done_when: new test runs the init --force --sample-data path twice against a temp profile and asserts the second run ends in a BatonError JSON envelope per the exit-code contract — no raw sqlite3.IntegrityError traceback — with the seed idempotent or duplicate-guarded
- confidence: medium
- notes: charter AUTOMATABLE entry 2 (error-envelope conformance in cli/) applies; either idempotent seed or a guarded error satisfies done_when — `src/baton/migrations/` is outside every LOAD_BEARING glob

## FQ-14: M16: PostgREST add_work เชื่อ Prefer: return=representation — ตอบว่าง → Work ไม่มี id เงียบๆ
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/14
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit; fallback `else work` returning the input Work without server id re-verified at `postgrest.py:306-310`)
- files_expected: src/baton/adapters/db/postgrest.py, tests/test_postgrest_add_work.py (new)
- load_bearing: false
- gate_level: full
- done_when: new test mocks PostgREST returning an empty representation body for add_work and asserts a contract error (UpstreamError) is raised rather than a Work with id=None coming back silently; success path unchanged
- confidence: high
- notes: adapters/db is not LOAD_BEARING (only adapters/chat is)

## FQ-21: M26: job supervise --id <ค่าใดก็ได้> สร้าง job dir ใหม่ (เขียน meta ก่อน validate)
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/21
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit; `_write_meta(job_id, ...)` as the first statement of supervise re-verified at `jobs.py:273`)
- files_expected: src/baton/core/jobs.py, tests/test_jobs_supervise_id.py (new)
- load_bearing: false
- gate_level: full
- done_when: new test calls supervise with an id that has no job dir and asserts the contract usage error is raised AND `state/jobs/<id>/meta.json` is not created; the spawn→supervise flow still passes existing tests
- confidence: medium
- notes: `spawn()` pre-creates the job dir and meta (`jobs.py:200-215`) before launching `job supervise --id`, so requiring an existing job cannot break the normal path — that is what makes this unambiguous

## FQ-23: M28: supervise ติด SIGTERM handler หลัง Popen ลูก — SIGTERM ช่วงนั้นทิ้ง meta ค้าง running
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/23
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: Popen at `jobs.py:294` precedes handler registration at `:308-310`)
- files_expected: src/baton/core/jobs.py, tests/test_jobs_signal_order.py (new)
- load_bearing: false
- gate_level: full
- done_when: in supervise() the SIGTERM/SIGINT handlers are installed before subprocess.Popen — proven by a new test that records call order via monkeypatched `signal.signal`/`Popen` — and existing job tests still pass
- confidence: medium
- notes: `_on_signal` already null-guards `child is not None`, so moving registration earlier is mechanically safe

## FQ-5: M2: FallbackStore.degraded ถูกเขียน แต่ไม่มี production caller อ่าน
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/5
- last_triaged: 2026-08-24
- repro: confirmed (grep at HEAD: writes at `fallback.py:37,44`, zero production readers — only a docstring mention)
- files_expected: src/baton/adapters/db/fallback.py, src/baton/cli/cmd_doctor.py
- load_bearing: false
- gate_level: full
- done_when: owner decides the consumer; either ≥1 production reader surfaces degraded state to the user (new test proves report-on-degraded) or the flag is removed with the decision recorded in the issue
- confidence: medium
- notes: the missing piece is a UX decision (who reports stale reads), not a bug fix

## FQ-8: M5: env override BATON__A กับ BATON__A__B ชนะตามลำดับ iteration — scalar→dict แปลงเงียบๆ
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/8
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit; `_env_overrides` iterating `os.environ.items()` re-verified at `config.py:55-75`)
- files_expected: src/baton/core/config.py
- load_bearing: false
- gate_level: full
- done_when: a deterministic precedence rule is implemented and conflicts surface loudly (error or warning); new test sets both variables and asserts the decided winner independent of os.environ iteration order
- confidence: medium
- notes: any fix defines new override semantics for every caller — precedence rule itself is the owner's call

## FQ-9: M10: Notion get_status ไล่ blocks ทั้งหน้าเพื่อนับ block_count — 2+ paginated calls ต่อเอกสาร
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/9
- last_triaged: 2026-08-24
- repro: confirmed (code read in review/findings.md; pagination walk for block_count in `notion.py`)
- files_expected: src/baton/adapters/docs/notion.py
- load_bearing: false
- gate_level: full
- done_when: approach decided by owner; either measured call count per get_status (counted in fake) drops with block_count and other behaviour unchanged, or the owner accepts the limitation and the issue closes with the reason recorded
- confidence: medium
- notes: same underlying issue as findings L12, which is already an owner question (rate limit)

## FQ-12: M14: exit code ของ batch ที่สำเร็จบางส่วนไม่สม่ำเสมอ: send=5, schedule=3, video=6
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/12
- last_triaged: 2026-08-24
- repro: confirmed (three exit sites read against exits.py contract; facts confirmed in findings, reinterpretation proposed in review-log)
- files_expected: src/baton/cli/cmd_send.py, src/baton/cli/cmd_calendar.py, src/baton/cli/cmd_video.py, README.md
- load_bearing: false
- gate_level: full
- done_when: one decision implemented across send/schedule/video — unified partial-batch exit proven by a new test per command, or the per-command difference documented in skills/README and verified against exits.py
- confidence: high
- notes: charter NEEDS_SPEC twice over: changing exit-code meaning for existing callers, and >5-file reach if unified

## FQ-13: M15: calendar list หั่นเวลาด้วย event.start[11:16] — สมมติ format ISO-with-T เดียว
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/13
- last_triaged: 2026-08-24
- repro: confirmed (partial — guard present at HEAD: `cmd_calendar.py:322` `if len(event.start) >= 16`; residual scope is date-only display formatting, not garbage slicing)
- files_expected: src/baton/cli/cmd_calendar.py
- load_bearing: false
- gate_level: full
- done_when: decided rendering for date-only events implemented (e.g. dedicated all-day marker instead of the full ISO date in the time field); new test feeds a date-only event through calendar list and asserts the output
- confidence: medium
- notes: smaller than the original finding — the harmful half is already guarded; what remains is a product decision

## FQ-15: M19: _SDR_1080P อ้าง tone-map HDR→SDR แต่ filter chain ไม่มี zscale/tone-map
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/15
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: comment at `ffmpeg.py:24-26` claims tone-map; chain is scale/pad/format only)
- files_expected: src/baton/adapters/media/ffmpeg.py
- load_bearing: false
- gate_level: full
- done_when: either the claim is corrected to match the actual filter chain (comment + preset naming consistent, new test asserts claim/chain consistency) or real tone-mapping is implemented with owner sign-off recorded
- confidence: high
- notes: option (ข) changes video delivered to learners — owner decision; option (ก) alone is a claim fix, but choosing between them is the spec work

## FQ-16: M20: webhook health ใช้ GET — receiver ตอบ 405 ก็ถือว่าผ่าน
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/16
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit; health failing only on status ≥500 re-verified in `adapters/chat/drivers.py`)
- files_expected: src/baton/adapters/chat/drivers.py
- load_bearing: true
- gate_level: deep
- done_when: health criterion decided and implemented so only responses proving the receiver answers webhooks count as healthy; new test covers the 405 case
- confidence: high
- notes: LOAD_BEARING (`src/baton/adapters/chat/**`) — human approval required and deep gates; note deep gates are currently fail-closed until pip-audit is installed, per charter §6

## FQ-17: M22: fake ต่างจากของจริง: set_current_piece และ delete_blocks เหมือนจริงไม่ครบ
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/17
- last_triaged: 2026-08-24
- repro: confirmed (run evidence 2026-08-16 audit; fake/real divergence in `tests/fakes.py` vs `adapters/db/sqlite.py:185-197` and Notion delete_blocks)
- files_expected: tests/fakes.py (existing — owner approval required), src/baton/adapters/db/sqlite.py, src/baton/adapters/docs/notion.py
- load_bearing: false
- gate_level: full
- done_when: fakes match the real adapters at both points, proven by a NEW test comparing fake vs real behaviour/error types; any edit to tests/fakes.py happens only with explicit owner approval recorded in the issue
- confidence: high
- notes: charter §3 forbids an unattended run editing an existing test file — the approval question is the spec gate (same pattern as the already-fixed M31)

## FQ-18: M23: branch "✗ NOT DELIVERED" ใน cmd_send ไร้ทางถึง — driver โยน exception เสมอ
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/18
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: `cmd_send.py:163` appends the marker when `sent` is absent; drivers raise on failure so the condition is unreachable)
- files_expected: src/baton/cli/cmd_send.py
- load_bearing: false
- gate_level: full
- done_when: decision implemented: dead branch removed with a new test covering the remaining output paths, or kept with a comment stating the real reachability condition
- confidence: high
- notes: delete-vs-keep is a judgment about future driver behaviour — owner call

## FQ-19: M24: เทสต์ thai combining marks เทียบ normalise(x)==normalise(x) — tautology
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/19
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD in `tests/test_resolve.py`)
- files_expected: tests/test_resolve.py (existing — owner approval required), tests/test_resolve_nfc.py (new, if chosen)
- load_bearing: false
- gate_level: full
- done_when: a test proves real NFC behaviour — two canonically-equivalent Thai input forms normalise to equal strings — and the tautological assertion is gone from the suite
- confidence: high
- notes: charter §3 forbids an unattended run editing an existing test file; owner must approve the edit (or choose new-file-plus-removal)

## FQ-20: M25: init ถามค่าตั้งต้น chat = telegram ขัดกับ defaults.yaml ที่ใช้ line
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/20
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: `_ask(ctx, "How are messages sent", "telegram", CHAT_DRIVERS)` at `cmd_init.py:252`)
- files_expected: src/baton/cli/cmd_init.py
- load_bearing: false
- gate_level: full
- done_when: single source of truth chosen by owner; the prompt default derives from it and a new test proves the prompt default equals the packaged default
- confidence: high
- notes: which default wins (`line` vs `telegram`) is a product decision even though the fix is one line

## FQ-22: M27: job stop คืน OK เสมอ แม้ต้อง SIGKILL — ซ่อน outcome จากผู้เรียก
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/22
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: `handle_stop` in `cmd_job.py:249-260`; payload keeps `status` while exit stays OK)
- files_expected: src/baton/cli/cmd_job.py, src/baton/core/jobs.py
- load_bearing: false
- gate_level: full
- done_when: one decision implemented: either documented read-the-payload contract (docs updated + new test asserting payload shape per outcome) or differentiated exit codes with a new test proving each
- confidence: high
- notes: differentiating exits changes exit-code meaning for existing callers — charter NEEDS_SPEC

## FQ-24: M30: init hardcode เล็กๆ: ไม่ถาม docs driver; SELECT count(*) ใช้ชื่อโต๊ะตายตัว
- disposition: ready-to-spec
- source: https://github.com/chunnytechmate/studio-baton/issues/24
- last_triaged: 2026-08-24
- repro: confirmed (code read at HEAD: `SELECT count(*) FROM learners` at `cmd_init.py:232`; no docs-driver question in the init prompt)
- files_expected: src/baton/cli/cmd_init.py
- load_bearing: false
- gate_level: full
- done_when: owner decision recorded; if behaviour changes (ask for docs driver / route the count through the mapping) a new test covers the changed path, and the acceptance is documented if kept as-is
- confidence: high
- notes: accept-and-document vs rewire is explicitly an owner call in the finding

## FQ-4: M1: alias ซ้ำใน contact เดียว → ระบบแจ้งกำกวมทั้งที่ชี้คนเดียวกัน
- disposition: needs-info
- source: https://github.com/chunnytechmate/studio-baton/issues/4
- last_triaged: 2026-08-24
- repro: confirmed (issue's line refs date from `fa8a041`; at HEAD the scoped bug does NOT reproduce — already fixed by `cf658ab`, exact matches keyed by contact key at `src/baton/adapters/chat/base.py:92-105`, covering test `tests/test_send.py:392` `test_a_duplicate_alias_under_one_contact_is_one_match` passes)
- files_expected: none (verify-only outcome — no change expected)
- load_bearing: true (any residual change would touch `src/baton/adapters/chat/**`)
- gate_level: deep
- done_when: human confirms fixed-at-HEAD and closes #4, or names a residual case — which then re-triages through the LOAD_BEARING route (ready-to-spec minimum, gate level deep)
- confidence: high
- notes: nothing left to implement or spec for the scoped bug; close-vs-residual is a call only the owner can make, hence needs-info

## Not covered

Items with a live label that is not a triage disposition — read live 2026-08-26,
not re-triaged because the contract hands the next decision to a human or a live
claiming run:

- **FQ-41 (P0/F2)** — `factory:in-progress`, claimed via branch `claude/fq-41`; draft
  PR #54 open (CI green) with no verify run recorded yet. If the owning run has ended,
  a human should decide whether it moves to `factory:awaiting-review` or back to the
  queue — triage does not disturb a live claim.
- **FQ-10 (M12)** — `factory:awaiting-review`, PR #37 open. Its entry above still shows
  the pre-review disposition `ready-to-implement`; that was correct when triaged and is
  what produced the now-open PR.
- **FQ-32 (P0/S1)** — `factory:awaiting-review`, PR #35 open and `factory:verified`.
  Human owns the merge decision; FQ-29 and FQ-33 unblock when it merges.

FQ-4 (M1), skipped by the 2026-08-23 cap, was triaged by run `2026-08-24T000806Z-triage-4`
(needs-info — already fixed at HEAD; see its entry above). FQ-1 (M6) has since closed and
needs no further triage.

Review queue: 3 items (#41→PR #54, #10→PR #37, #32→PR #35) against the charter stop
limit of "more than 3 awaiting human review" — at the limit, not over it.

## FQ-31: P0/S0 freeze staged Song DB context for lesson contracts
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/31
- last_triaged: 2026-08-24
- repro: confirmed (Gate 2 found live lookup after staging)
- files_expected: src/baton/pipelines/staging.py, src/baton/cli/cmd_lesson.py, tests/test_piece_snapshot.py
- load_bearing: false
- gate_level: full
- done_when: stage A, assign B, and contract still exposes A without live current_piece_id; explicit none/unavailable persist and invalid state fails closed
- confidence: high
- notes: spec handoff 2026-08-24T21:23:27Z; only this slice is initially claimable

## FQ-32: P0/S1 publish frozen Song DB resources safely
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/32
- last_triaged: 2026-08-24
- repro: confirmed (publisher currently renders summary only)
- files_expected: src/baton/render/piece.py, src/baton/pipelines/publish.py, src/baton/pipelines/staging.py, src/baton/cli/cmd_lesson.py, tests/test_piece_publish.py
- load_bearing: false
- gate_level: full
- done_when: frozen resources precede summary; exact same-snapshot dedup is safe; changed/unknown forced republish makes no write
- confidence: medium
- notes: blocked by merge of FQ-31; block ownership has no persisted ids

## FQ-33: P0/S2 send published practice track and document operations
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/33
- last_triaged: 2026-08-26
- repro: not-attempted (blocked-on-predecessor slice; predecessor merge state read live on GitHub)
- files_expected: src/baton/pipelines/send.py, tests/test_send.py, tests/test_lesson_piece_flow.py, README.md, docs/notion-setup.md
- load_bearing: true
- gate_level: deep
- done_when: after publishing song A and assigning live song B, contract, document, published record, and message still use A; legacy-unavailable never falls back and the configured gate warns or blocks; current instrument remains live; README and Notion setup document snapshot timing, block shapes, preservation, and forced-republish remedy
- confidence: medium
- notes: blocked by merge of FQ-32 (PR #35 open, unmerged); owner approved only the snapshot-fixture and obsolete live-song assertion replacement in tests/test_send.py — Draft plus human read required; reconfirmed 2026-08-26

## FQ-29: P0 lesson summaries can drift to a learner's newer song
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/29
- last_triaged: 2026-08-26
- repro: not-attempted (spec-parent umbrella — no file-level work tracked on this issue; slice blockers read live on GitHub)
- files_expected: none directly — file scope lives on the slice handoffs (#32, #33)
- load_bearing: false (this issue edits nothing; slice #33 declares load_bearing: true, deep gates, Draft + human read)
- gate_level: full
- done_when: slices #32 and #33 are merged and closed; a human then closes umbrella #29 — no file-level work is tracked on this issue
- confidence: high
- notes: owner approved Gates 1-4 on 2026-08-25 (spec PR #30 merged); blocked by slice #32 (PR #35 open, factory:verified — human owns merge) then #33

## FQ-48/FQ-52 corrected recovery chain (re-triaged 2026-08-26)

Entries below were re-triaged by run `2026-08-26T001142Z-triage-queue`. Order and scope
come from the owner-approved corrected spec (FQ-48, then the FQ-52 evidence-boundary
correction merged as PR #53); the whole chain roots at #41 and unblocks in the order
listed. `done_when` and `files_expected` are quoted from each issue's live
`factory-handoff:v1` comment, which was written by the approved spec — those comments
remain the operational handoff.

## FQ-40: P0/F1 track the factory authority in origin/main
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/40
- last_triaged: 2026-08-26
- repro: not-attempted (tracked-authority bootstrap slice; nothing runnable until its blocker merges — blocker state read live on GitHub)
- files_expected: AGENTS.md, CLAUDE.md, docs/factory/CONTRACT.md, docs/factory/CHARTER.md, docs/factory/runs/README.md, docs/factory/runs/<UTC>-bootstrap-40.md
- load_bearing: true
- gate_level: bootstrap (per approved spec — not one of the charter §6 levels; see run record)
- done_when: a clean origin/main contains complete concise AGENTS/Claude authority, contract, charter, and run schema authored from merged FQ-48; the exact bootstrap allowlist and expiry agree; app-bound CI and FACTORY_BOOTSTRAP are green; and a cold critic accepts
- confidence: medium
- notes: blocked by #41 (factory:in-progress, draft PR #54 open, CI green, no verify run recorded); next to unblock when #41 merges

## FQ-42: P0/F3 make local factory gates catch CI formatting drift
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/42
- last_triaged: 2026-08-26
- repro: not-attempted (bootstrap slice blocked two levels up the chain — blocker state read live on GitHub)
- files_expected: .factory/gates.conf, .claude/scripts/gates.sh, .factory/scripts/bootstrap-tools.sh, tests/test_factory_gate_parity.py, docs/factory/runs/<UTC>-bootstrap-42.md
- load_bearing: true
- gate_level: bootstrap (per approved spec — not one of the charter §6 levels; see run record)
- done_when: tracked gate config/runner and isolated pinned pip-audit provisioning work from a clean checkout; candidate deep gates are GREEN; unformatted Python/Markdown and missing tooling fail closed; and project dependency files remain unchanged
- confidence: medium
- notes: blocked by #40; every file it touches is LOAD_BEARING — deep-gate fail-closed posture applies until #42 itself lands

## FQ-49: P0/B3 track independent factory proof and verifier tooling
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/49
- last_triaged: 2026-08-26
- repro: not-attempted (bootstrap slice blocked up the chain — blocker state read live on GitHub)
- files_expected: .factory/scripts/prove-test.sh, .claude/skills/factory-verify/SKILL.md, .agents/skills/factory-verify/SKILL.md, tests/test_factory_verifier_protocol.py, docs/factory/runs/<UTC>-bootstrap-49.md
- load_bearing: true
- gate_level: bootstrap (per approved spec — not one of the charter §6 levels; see run record)
- done_when: tracked prove-test and Claude/Codex verifier adapters accept exact negative proof and reject base/head drift, dirty checkout, proof failure, or evidence mismatch under merged deep gates
- confidence: medium
- notes: blocked by #42; corrected FQ-48 Gate 4 Slice 4 (supersedes the FQ-38 ordering still cited in the issue body)

## FQ-50: P0/B4 make approved load-bearing handoffs executable
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/50
- last_triaged: 2026-08-26
- repro: not-attempted (bootstrap slice blocked up the chain — blocker state read live on GitHub)
- files_expected: .claude/skills/factory-implement/SKILL.md, .agents/skills/factory-implement/SKILL.md, docs/factory/CONTRACT.md, tests/test_factory_implement_protocol.py, docs/factory/runs/<UTC>-bootstrap-50.md
- load_bearing: true
- gate_level: bootstrap (per approved spec — not one of the charter §6 levels; see run record)
- done_when: tracked Claude/Codex implement adapters execute only explicitly approved load-bearing handoffs under deep gates and fresh verification; generic workers cannot claim bootstrap items; old handoffs remain valid; and merge irreversibly expires FQ-48 recovery
- confidence: medium
- notes: blocked by #49; merging it expires the FQ-48 recovery protocol — irreversible step, human merge required

## FQ-39: P0/F0 prove factory enforcement topology with synthetic payloads
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/39
- last_triaged: 2026-08-26
- repro: not-attempted (tracer-bullet proof against synthetic GitHub payloads — no product behavior to reproduce; blocker state read live on GitHub)
- files_expected: .factory/scripts/github_enforcement.py, tests/test_factory_github_enforcement.py, docs/factory/GITHUB.md
- load_bearing: true
- gate_level: deep
- done_when: synthetic GitHub payload tests prove exact green check/app inventory, zero-check bootstrap and strict rule previews with PR-only/no-force/no-delete and auto-merge off, explicit apply, and denied merge/auto-merge/push/ruleset/force/delete probes without live mutation
- confidence: medium
- notes: blocked by #50; issue body still cites "FQ-38 Gate 4 Slice 0" — the corrected FQ-48 order in the live handoff comment governs

## FQ-43: P0/F4 append immutable factory evidence without audit PRs
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/43
- last_triaged: 2026-08-26
- repro: not-attempted (factory-infrastructure slice blocked up the chain — blocker state read live on GitHub)
- files_expected: .factory/scripts/audit_record.py, tests/test_factory_audit.py, docs/factory/runs/README.md
- load_bearing: true
- gate_level: deep
- done_when: run/readiness records append as unique first-introduction blobs on the audit branch with bounded non-fast-forward retry; mutation/deletion is detected as MISCONFIGURED and audit-only writes open no PR
- confidence: medium
- notes: blocked by #39; changes how this run-record directory itself is written — read its handoff before executing

## FQ-44: P0/F5 keep work agent-owned until exact CI and verification agree
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/44
- last_triaged: 2026-08-26
- repro: not-attempted (factory-infrastructure slice blocked up the chain — blocker state read live on GitHub)
- files_expected: .claude/skills/factory-implement/SKILL.md, .claude/skills/factory-spec/SKILL.md, .agents/skills/factory-implement/SKILL.md, .agents/skills/factory-spec/SKILL.md, tests/test_factory_readiness.py, docs/factory/CONTRACT.md
- load_bearing: true
- gate_level: deep
- done_when: old handoffs remain valid; canonical progress/resume rejects author/branch/SHA/counter drift; PRs remain Draft/in-progress through CI and exact-head verification; append-before-label readiness is required; and standard specs need one packet approval while high-risk specs retain four
- confidence: medium
- notes: blocked by #43; edits the factory contract and both harnesses' skills — pure LOAD_BEARING

## FQ-45: P0/F6 move triage monitor and tune evidence off the merge queue
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/45
- last_triaged: 2026-08-26
- repro: not-attempted (factory-infrastructure slice blocked up the chain — blocker state read live on GitHub)
- files_expected: .claude/skills/factory-triage/SKILL.md, .claude/skills/factory-monitor/SKILL.md, .claude/commands/factory-tune.md, .agents/skills/factory-triage/SKILL.md, .agents/skills/factory-monitor/SKILL.md, .agents/skills/factory-tune.md, tests/test_factory_audit_routines.py
- load_bearing: true
- gate_level: deep
- done_when: triage monitor and tune preserve live issue handoffs but write full audit-branch records without PRs and fail closed on audit-integrity or credential failure
- confidence: medium
- notes: blocked by #44; this triage skill itself is in its files_expected — the run that executes it edits the rules this run followed

## FQ-46: P0/F7 activate enforced factory flow and truthful control-room counts
- disposition: wait-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/46
- last_triaged: 2026-08-26
- repro: not-attempted (activation slice blocked up the chain — blocker state read live on GitHub)
- files_expected: .claude/commands/factory.md, .agents/skills/factory-status/SKILL.md, .factory/scripts/github_enforcement.py, .factory/scripts/doctor.sh, tests/test_factory_activation.py, docs/factory/GITHUB.md
- load_bearing: true
- gate_level: deep
- done_when: fork/coordinator permission probes deny merge auto-merge upstream push ruleset mutation and protected force/delete; exact green app-bound contexts are strict required checks; current open PRs are reconciled; control room counts current-SHA accepted/rejected PRs plus named needs-info and reports enforcement active only after all probes pass
- confidence: medium
- notes: blocked by #45; last link of the chain — flips the factory to enforced mode, so nothing may skip ahead of it
