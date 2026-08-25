# FQ-38 — Architecture

## Scope and migration oracle

This migration separates early local feedback from authoritative remote enforcement.
Local gates are never described as equivalent to the full CI matrix.

The preventive oracle is the required GitHub Actions check set produced by GitHub's
synthetic pull-request merge ref against the current `main`, with strict base freshness and
each context bound to the trusted GitHub Actions app. Independent verification separately
proves the exact source head against its recorded base. A successful push check on `main`
is post-merge health evidence, not a preventive oracle.

Flow success is measured for standard cohesive features. Routine fixes need only the final
merge-or-close decision. A standard feature that needs product direction receives one
complete spec-pack decision and one final merge-or-close decision. High-risk or
load-bearing work may retain more approval gates; FQ-38 itself uses the current four-gate
bootstrap because it changes the safety system.

## Permission boundary

GitHub currently sees the agent as the repository owner, so branch protection alone cannot
be a complete boundary: the same credential can administer or remove it. The target model
uses a separate least-privilege factory credential with repository contents, issues, pull
requests, checks/actions read, and metadata permissions, but no repository administration,
ruleset bypass, release, environment, secret, or package administration. The owner keeps
the administrative credential outside agent environments.

Until credential separation is complete, repository rules add accidental protection but
the contract and hooks remain the only barrier against an agent changing those rules. The
factory must report that state as `enforcement: partial`, never `enforcement: active`.

## Systems touched

- GitHub repository settings: protect `main`; protect the append-only audit branch; bind
  required contexts only after observing their exact names and app identifiers on green CI.
- Local factory gate runner: add Ruff format checking to the required lint judgment while
  retaining the existing gate levels and summary shape.
- Implementation workflow: keep work Draft and agent-owned through CI and verification;
  transition to human review only after both accept the same current head/base.
- Specification workflow: add a standard one-decision spec pack while retaining the
  current four gates for load-bearing/high-risk work.
- Contract and charter: define readiness, recovery, audit storage, credential state, and
  cohesive feature delivery without weakening merge, load-bearing, existing-test, or
  independent-verifier boundaries.
- Control room and monitor: count actual human decisions and reconcile partial state writes.

`.github/workflows/**` and `.factory/gates.conf` are not changed in the first migration.
The existing CI workflow remains authoritative; local commands are aligned only where they
are reproducible on one workstation.

## Gate layers

The local full gate provides reproducible early feedback:

1. Ruff lint plus Ruff format check over the same repository scope used by CI.
2. Mypy using the repository configuration.
3. The complete local pytest suite on the available supported interpreter.

GitHub remains authoritative for its synthetic merge ref, six interpreter/platform test
jobs, coverage invocation, leak/history scan, and trusted-app context. The exact required
context inventory and GitHub Actions app id are captured as migration evidence before any
required-check rule is enabled. Strict required checks force a stale branch to update and
rerun before merge.

Load-bearing migration work uses the existing deep gate. `pip-audit` is provisioned as a
factory execution tool in the local/verifier environment, not added to Studio Baton's
runtime or locked project dependencies. If the audit tool cannot run, deep remains
`MISCONFIGURED` and implementation stops.

## Delivery model and budgets

The existing `factory-handoff:v1` remains compatible. A spec-approved standard feature may
add:

```text
delivery: cohesive
checkpoints: <ordered acceptance identifiers>
```

A cohesive item is still one GitHub queue issue, one deterministic branch, one
implementation run, and one PR. Checkpoint completion is persisted in one editable
`factory-progress:v1` comment on the source issue so an interrupted run can resume without
inventing state. Every source-head change invalidates prior CI and verifier evidence.

The current 400 changed-line stop remains unchanged during FQ-38. Counting is additions
plus deletions from the recorded merge base using text numstat; binary files, renames whose
count cannot be established, and scope-category ambiguity require a human read. A feature
that cannot fit is not eligible for cohesive delivery and must be sliced under the old
model. Raising the limit requires a later `factory-tune` decision backed by completed-run
evidence; FQ-38 does not pre-authorize 800 or 1,200-line PRs.

Standard specs change from four interruptions to one complete packet: product,
architecture, critic result where required, program design, and slices are presented
together for one explicit direction approval. Load-bearing/high-risk specs retain the four
separate gates. Queue writes follow the same approved packet and do not add another
approval request.

## Readiness state machine

1. **Building** — source issue is `factory:in-progress`; PR is Draft; no review-result
   label. Draft creation is a progress notification, not an owner decision.
2. **CI pending/red** — the issue remains in progress. One bounded repair cycle is allowed
   on the same queue item. A changed head reruns local gates, CI, and verification.
3. **Verifier rejected** — one bounded repair cycle is allowed. A second rejection, or the
   same gate failure twice, leaves the Draft PR labeled `factory:rejected`, moves the issue
   to the appropriate named human/blocking state, and records the exact question.
4. **Infrastructure failure** — no verified label is applied. The issue becomes
   `factory:needs-info` only when a named owner/external action is genuinely required;
   otherwise it remains agent-owned for bounded retry.
5. **Ready** — required GitHub checks are terminal-green on the current synthetic merge
   ref, the source branch contains the current strict base, and a fresh verifier accepts
   the exact source head. An immutable readiness record stores source SHA, base SHA, check
   run URL, verifier verdict, gate line, and human-read flag. Only then is
   `factory:verified` applied and the issue moved to `factory:awaiting-review`.
6. **Human decision** — when no human read is required the agent may mark the PR ready for
   review; otherwise it remains Draft until the owner reads it. The owner alone merges,
   closes, or enables auto-merge.

The durable readiness record is written before labels. Label writes are idempotent. The
monitor reconciles a partial transition from the record and exact SHAs; it never infers
GREEN from a label. If `main` advances, strict checks invalidate readiness, the issue
returns to building, and the updated head is re-verified.

The true human-decision count is the union of open PRs carrying a current-SHA accepted or
rejected readiness record and open issues with a named `needs-info` question. Pending/red
Draft PRs without such a record remain agent-owned. Audit activity is reported separately.

## Durable audit evidence

Audit-only triage, monitor, tune, and status runs do not open PRs. They append the same full
run-record files used today to a dedicated `factory-audit` Git branch. The branch is not
merged into `main`; it is protected from force push and deletion. Factory credentials may
create unique files under `docs/factory/runs/` but have no ruleset administration.

Protocol:

1. Fetch the current audit head and verify existing records are unchanged.
2. Add exactly one unique run-record file; modifying or deleting an existing audit record
   fails closed.
3. Push without force. On a non-fast-forward race, rebase the unique addition and retry a
   bounded number of times.
4. Live issue labels/comments remain the operational state; the audit branch is historical
   evidence only.

Existing records on `main` remain valid. No periodic merge is required, so audit retention
does not create a human merge decision. Disabling audit-branch protection or rewriting its
history is an owner-only break-glass action.

## Bootstrap order

1. Record the owner's exact one-time approvals for protected policy paths and the
   formatting-only change to `tests/test_piece_snapshot.py`. The PR stays Draft and needs a
   human read; assertions and behavior may not change.
2. Create `main` PR-only/admin-enforced/no-force/no-delete protection with zero required
   checks. This prevents direct pushes while avoiding a red-check lockout.
3. Provision `pip-audit` as factory tooling and prove the deep gate is configured before
   changing protected policy.
4. Repair the current formatting baseline and clarify the interactive existing-test rule
   in one Draft PR. Run deep gates and independent verification. The owner performs the
   bootstrap merge under zero-required-check protection after reading the mechanical diff.
5. Reconcile open PRs onto the green baseline and observe one fully green check inventory,
   including exact context names and trusted app ids.
6. Add strict required checks to `main` protection, verify the rule through the API, and
   run a harmless negative probe demonstrating that a red required check blocks merge.
7. Create and protect `factory-audit`, migrate only future audit records to it, then change
   readiness, control-room, standard-spec, and cohesive-delivery behavior.
8. Replace the owner-level agent credential with the least-privilege factory credential.
   Enforcement becomes `active` only after permission probing confirms ruleset changes and
   merges are denied to that credential.

Steps that change repository rules are recorded with the before/after payload and an
owner-only rollback command. Required checks are never enabled from guessed names.

## External dependencies and owner setup

- One factory execution tool: `pip-audit`, isolated from project dependencies.
- One least-privilege GitHub App or fine-grained token created by the owner; credential
  creation and removal of the owner token from agent environments cannot be delegated to an
  agent using that same credential.
- Existing GitHub Actions, issue/PR APIs, labels, hooks, and fake test data.

No learner record, real transcript/video, outbound message, release, runtime dependency, or
vendor integration is involved.

## Load-bearing paths involved

- `docs/factory/CONTRACT.md`
- `docs/factory/CHARTER.md`
- `.claude/scripts/gates.sh`
- `.claude/skills/factory-spec/SKILL.md`
- `.claude/skills/factory-implement/SKILL.md`
- `.claude/commands/factory.md` and monitor/tune command text that writes audit records
- matching `.agents/skills/` adapters when their contract-facing behavior changes
- GitHub `main` and `factory-audit` protection/ruleset settings
- existing `tests/test_piece_snapshot.py`, formatting only during bootstrap

The exact protected-file list is finalized at Gate 3. No product contracts, chat adapters,
real profiles, release files, or dependency locks are in scope.

## Failure and rollback

- A misspelled required context is recovered by the owner temporarily removing only that
  context through the documented settings/API payload; PR-only/no-force/no-delete
  protection remains active.
- A CI or verifier failure follows the bounded readiness transitions above; it is not
  reclassified as human-ready merely to drain the queue.
- An interrupted cohesive run resumes from the exact progress comment and remote SHAs or
  fails closed if they disagree.
- An audit push race retries only the unique new file; existing audit history is never
  rewritten.
- Policy rollback is a reviewed revert PR. `main` PR-only protection, the human merge
  boundary, existing-test approval, load-bearing protection, and independent verification
  are never disabled as a rollback shortcut.
- Active cohesive items finish under their recorded handoff or require an explicit owner
  decision to split; rollback does not silently reinterpret them.
