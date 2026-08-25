# FQ-38 — Architecture

## Oracle and decision budget

Local gates are early feedback, not equivalent to CI. The preventive oracle is the required
GitHub Actions set on GitHub's synthetic PR merge ref against current `main`, using strict
base freshness and check contexts bound to the GitHub Actions app. Independent verification
separately proves the exact source SHA against its recorded base. A `main` push check is
post-merge health evidence only.

Routine fixes need one final owner decision. Standard cohesive features get one complete
spec-pack decision and one final decision. High-risk/load-bearing specs retain the current
four gates; FQ-38 uses them because it changes the safety system.

## Permission boundary

The current owner credential can administer rules and merge, so protection alone is partial.
The target is fork-based with two non-owner credentials: a builder has Contents write only
on an owner-controlled fork; an upstream coordinator has Issues/Pull Requests write and
Contents/Checks/Actions read on Studio Baton, but no upstream Contents write or repository
administration. Builder branches and audit history live on the protected fork; cross-fork
PRs target upstream `main`. The merge API requires upstream Contents write, so neither
credential can merge. Repository auto-merge is disabled so Pull Requests write cannot arm
a future merge. The owner keeps upstream administration outside agent environments.
Status remains `enforcement: partial` until probes prove both credentials cannot merge,
enable auto-merge, push upstream `main`, change rules, force-push, or delete protected
branches.

## Systems and gate layers

- Protect `main` and a dedicated audit branch through GitHub settings.
- Add Ruff format checking to the local required lint judgment; retain gate levels/summary.
- Keep implementation Draft and agent-owned through CI and fresh verification.
- Add a one-decision standard spec pack; keep four gates for high-risk/load-bearing work.
- Update contract, charter, control room, monitor, and relevant Claude/Codex skill adapters.

Local full remains lint+format, mypy, and the full local pytest suite. CI remains authoritative
for its synthetic merge ref, six interpreter/platform jobs, coverage invocation, and
leak/history scan. Exact required context names and GitHub Actions app ids are recorded from
one green run before rules require them. `.github/workflows/**` and `.factory/gates.conf`
remain unchanged initially.

Deep work provisions `pip-audit` as factory execution tooling, not a Studio Baton project
dependency. If it cannot run in implementation and verifier environments, deep is
`MISCONFIGURED` and work stops.

## Delivery and budget

`factory-handoff:v1` stays compatible and may add:

```text
delivery: cohesive
checkpoints: <ordered acceptance identifiers>
```

A cohesive item is one issue, deterministic branch, run, and PR. Progress persists in one
`factory-progress:v1` source-issue comment containing run id, fork branch, source/base SHAs,
checkpoint states, and gate/verifier repair counters; it is operational, not evidence.
Every source-SHA change invalidates prior CI and verifier evidence. A resumed session does
not reclaim: it writes `resume_of`, verifies the issue is still in progress and the remote
SHA equals the progress comment, then continues; disagreement fails closed. The current
400-line stop remains: additions plus deletions from the recorded merge base.
Binary/ambiguous rename changes require human review. Oversize work uses the old slices. A
higher limit needs a later evidence-backed `factory-tune` decision.

Standard specs present product, architecture, critic result, design, and slices as one
packet for one explicit approval; approved queue writes add no extra stop. High-risk and
load-bearing specs retain separate approvals.

## Readiness state machine

1. **Building:** issue `in-progress`, Draft PR, no review-result label.
2. **CI red/pending:** remains agent-owned; one repair cycle. New SHA reruns all evidence.
3. **Verifier rejected:** one repair cycle; second rejection/failure becomes a named blocked
   question with Draft PR `factory:rejected`. The same CI/gate failure twice follows this
   rejected path.
4. **Infrastructure failure:** no verified label; `needs-info` only for a named external or
   owner action, otherwise bounded agent retry.
5. **Ready:** strict required checks are terminal-green and a fresh verifier accepts the
   current source/base. An append-only readiness record stores issue/PR, source/base/merge
   SHAs, check contexts/run URLs, gate/proof lines, verdict, timestamp, and human-read flag
   before labels. Then, and only then, apply
   `factory:verified` and `awaiting-review`.
6. **Human decision:** agent may mark ready only when no human read is required; otherwise
   it stays Draft. Owner alone manually merges or closes; repository auto-merge stays off.

Readiness records live at `docs/factory/readiness/<pr>-<source-sha>.md` on the audit branch.
Accepted work requiring a human read is counted as a decision while remaining Draft; the
owner marks it ready after reading. Label writes are idempotent and reconciled from the
readiness record, never vice versa.
Strict base advancement invalidates readiness and requires updated CI/verification. The
human queue is current-SHA accepted/rejected PRs plus named `needs-info` questions; pending
Drafts remain agent-owned and audit work is separate.

## Durable audit evidence

Audit-only routines append the existing full run-record format to protected branch
`factory-audit` and open no PR. Each push adds exactly one unique
`docs/factory/runs/<run-id>.md`; existing records may not change. Non-fast-forward races
rebase only the unique addition and retry boundedly. The branch forbids force push/deletion.
Live issue state remains operational; the audit branch is historical only. Existing records
on `main` remain valid and no periodic merge is required. A record's immutable value is the
first-introduction blob in protected history; readers verify the branch-tip blob still
matches it. Any mutation/deletion marks audit integrity `MISCONFIGURED` and stops the
factory, even though the original blob remains recoverable from history.

## Bootstrap

1. Gate 4 records protected policy scope, but it does not pre-authorize an existing-test
   edit. At the actual interactive baseline implementation, the owner must explicitly
   approve formatting-only `tests/test_piece_snapshot.py` in that same session or make the
   edit personally. Its Draft PR needs human read and cannot alter assertions.
2. Enable admin-enforced PR-only/no-force/no-delete `main` protection with zero required
   checks, avoiding red-baseline lockout.
3. Provision `pip-audit`; prove deep gates work before protected policy changes.
4. Repair formatting and clarify the interactive existing-test rule in one Draft PR; deep
   gates and a fresh verifier must pass before the owner bootstrap-merges it.
5. Reconcile open PRs, observe one green exact check/app inventory, then require those
   contexts strictly and prove a harmless red probe blocks merge.
6. Create the owner-controlled fork, its protected `factory-audit` branch, and the two
   least-privilege credentials; then change readiness, reporting, standard-spec, and
   cohesive-delivery behavior.
7. Remove the upstream owner credential from agent environments. Enforcement becomes active
   only after upstream merge/auto-merge/push/ruleset and fork force-push/delete probes are
   denied.

Every ruleset mutation records before/after payload and an owner-only recovery command.
Required contexts are never guessed.

## Dependencies, protected scope, and rollback

External setup is isolated `pip-audit`, one owner-controlled fork, a fork-scoped builder
credential, and an upstream read/coordinator credential without Contents write. No learner
data, real media/message, release, runtime dependency, or dependency lock is involved.

Load-bearing scope: `docs/factory/{CONTRACT,CHARTER}.md`, `.claude/scripts/gates.sh`, factory
spec/implement skills, factory control/monitor/tune commands, matching `.agents/skills/`
adapters, GitHub rules, and formatting-only `tests/test_piece_snapshot.py`. Gate 3 finalizes
exact paths. Product contracts, chat adapters, real profiles, and release files stay out.

Misspelled contexts are owner-recovered by removing only that context; PR-only/no-force/
no-delete protection stays active. Policy rollback is a reviewed revert PR. Active cohesive
items follow their recorded handoff or require owner-approved splitting. Existing tests,
load-bearing protection, human merge ownership, and independent verification are never
disabled as rollback shortcuts.
