# FQ-38 — Architecture

## Migration oracle

The oracle is the required GitHub Actions result on the exact pull-request head and on the
resulting `main` commit. Factory readiness is equivalent only when the local full gate,
negative proof, independent verification, and every required GitHub check agree. A local
GREEN result alone is never an oracle.

Flow behavior is characterized from live GitHub state: one approved feature bundle enters
implementation once, produces one reviewable result, and asks the owner for one final
merge-or-close decision. Audit-only routines must leave durable evidence without creating
another pending merge decision.

## Systems touched

- Local factory gates: make formatting part of the same required lint judgment as GitHub
  CI, without adding a dependency or changing the existing gate levels.
- Implementation workflow: open work before final readiness, wait for terminal GitHub
  checks, then apply `factory:verified` and move the source issue to human review only when
  both CI and the independent verifier accept.
- Specification workflow: approved vertical slices remain testable checkpoints, but a
  cohesive feature uses one queue item, one branch, and one implementation PR by default.
- Factory contract and charter: define the cohesive-feature exception, separate code/test
  and evidence diff budgets, audit-only evidence, and the true human-decision queue.
- Control-room reporting: count open verified/rejected factory PRs that actually require a
  human decision; red or pending work remains agent-owned and audit-only evidence is not a
  review item.
- GitHub repository settings: protect `main`, require pull requests and the existing CI
  checks, disallow force pushes/deletion, and apply the rule to administrators. Required
  review approvals remain zero because the owner is the sole reviewer and merge decision.

## State and record shapes

The existing `factory-handoff:v1` fields remain valid. A spec-approved cohesive feature
adds optional fields; old consumers may ignore them:

```text
delivery: cohesive
slices: <ordered slice identifiers>
code_test_budget: 800
evidence_budget: 400
```

Routine single fixes retain the current 400 changed-line total. A cohesive feature must be
explicitly approved at spec Gate 4; it may use at most 800 changed code/test lines plus 400
factory evidence lines. Load-bearing and existing-test rules are unchanged.

Audit-only routines write a dedicated GitHub comment instead of opening a PR:

```text
<!-- factory-run:v1 -->
run_id: <unique UTC id>
stage: triage | monitor | tune | status
status: succeeded | stopped | infrastructure-failed
started_at: <UTC>
finished_at: <UTC>
gate_status: GREEN | RED | MISCONFIGURED | not-run
human_required: true | false
summary: <bounded single-line result>
```

The comment lives on a dedicated open factory-audit issue. Operational label/handoff
changes remain on their source issues. A scheduled consolidated snapshot may copy these
records into the repository, but the snapshot does not block routine flow.

## End-to-end flow

1. Intake identifies either a routine single fix or a cohesive feature needing a spec.
2. A spec-approved feature creates one implementation queue item containing ordered slice
   checkpoints and one combined `done_when` condition.
3. The implementation run claims one deterministic branch and completes the checkpoints
   as separate commits, using fake data and negative proofs.
4. The local required gate includes lint, format parity, types, and tests. Any disagreement
   blocks the run.
5. A PR is opened before final readiness so GitHub checks run. While checks are pending or
   red, the work remains agent-owned and does not consume the human-decision queue.
6. A fresh verifier reviews the exact remote head. The head must not change after the
   accepted verdict without re-verification.
7. Only terminal-green required GitHub checks plus an accepted verifier may apply
   `factory:verified` and move the issue to `factory:awaiting-review`.
8. The owner makes the final merge-or-close decision. An agent never enables auto-merge or
   merges; the owner may enable GitHub auto-merge as that final decision.
9. Triage, monitor, and tune runs update live state and append a bounded audit comment;
   they do not open an immediate-decision PR.

## Migration order

1. Repair the current formatting baseline with mechanical-only changes. The existing test
   edit requires explicit owner approval, a Draft PR, and a human read.
2. Add local format parity and prove that the local full gate fails on the old baseline and
   agrees with GitHub after repair.
3. Protect `main` only after the required check names have been observed on a green run;
   verify the rule through the GitHub API before relying on it.
4. Change readiness timing and control-room counting.
5. Enable cohesive-feature handoffs and audit-comment evidence.
6. Reconcile open PRs onto the green baseline, re-run verification where heads change, and
   continue FQ-29 without weakening its approved product behavior.

Each step is independently reversible. Repository rules are created last among safety
prerequisites so a misspelled required check cannot lock the repair path.

## External dependencies

No new package, service, credential, learner record, or messaging integration is needed.
The design uses the existing GitHub API/CLI, Actions checks, issue comments, labels, local
toolchain, and fake test data.

## Load-bearing paths involved

- `docs/factory/CONTRACT.md`
- `docs/factory/CHARTER.md`
- `.claude/scripts/gates.sh`
- `.claude/skills/factory-spec/SKILL.md`
- `.claude/skills/factory-implement/SKILL.md`
- `.claude/commands/factory.md`
- matching `.agents/skills/` adapters if their behavior text changes
- GitHub `main` branch protection/ruleset settings
- the pre-existing `tests/test_piece_snapshot.py`, formatting only, during baseline repair

`.factory/gates.conf`, `.github/workflows/**`, product contracts, chat adapters, and real
profiles are deliberately not changed unless a later gate proves the stated design cannot
be implemented without them.

## What could break elsewhere

- A required-check name can drift or be misspelled and block every merge; protection is
  enabled only after an exact green check inventory and has a documented rollback.
- Old routines may assume every run record is a repository file or every slice has its own
  issue. Adapters must accept both old and optional cohesive/audit forms during migration.
- Waiting for remote checks keeps an agent run alive longer, but it removes premature human
  handoffs rather than adding owner work.
- Larger cohesive diffs can become hard to review. The higher budget is spec-only, split
  into checkpoint commits, and never relaxes load-bearing or existing-test review.
- GitHub comments are less immutable than Git commits. Bounded audit comments retain the
  platform history; consolidated snapshots provide periodic repository retention without
  becoming a synchronization boundary.
- Open PRs created under the old readiness semantics may carry misleading labels. Migration
  must re-evaluate them against terminal CI before counting them as human decisions.

## Rollback

Disable the new repository ruleset through the GitHub settings/API, revert the factory
policy PR, and return routines to per-slice issues and file run records. Product code and
learner data are not migrated, so rollback does not require a data conversion.
