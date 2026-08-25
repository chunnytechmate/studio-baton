# FQ-48 — Bootstrap recovery architecture

## Oracle and authority boundary

FQ-48 does not pretend the missing Factory can verify itself. After this spec is approved
and merged, its architecture, design, and slices are the immutable temporary recovery
protocol. They authorize only the exact recovery issue numbers, paths, order, verdicts,
and expiry recorded here. Gate 4 records candidate blob ids while every item remains
waiting. Activation later attaches the verified FQ-48 merge SHA; a mismatch fails closed.

The temporary protocol may run only in this continuing interactive owner session. It is
not an unattended Factory path and cannot be resumed by a stranger without a fresh owner
authorization. Gate approvals decide the design; the one bootstrap authorization is the
later, exact approval to format the existing test. Gate 4 does not pre-authorize that edit.
This one-time limitation is honest: fresh unattended workers become supported only after
the recovery lane finishes.

Trust advances through three independent oracles:

1. Baseline repair requires every GitHub CI check green on the exact synthetic merge SHA,
   semantic equivalence of the formatted test, and a cold critic.
2. Authority adoption requires green CI, an exact path/line budget, and a cold critic
   against the merged FQ-48 protocol.
3. Candidate gate tooling must produce a real deep `FACTORY_GATES` GREEN verdict before
   it merges. Every later load-bearing item uses the merged runner and normal deep gates.

No bootstrap PR may merge with red/pending/missing CI, a `MISCONFIGURED` gate, or an
unreviewed head.

## Temporary recovery verdict

Positions before a trusted gate runner use a separate, fail-closed verdict:

```text
FACTORY_BOOTSTRAP: position=<1|2> status=GREEN head=<40-hex> merge=<40-hex>
checks=<exact app-bound check ids> critic=<run-id> semantic=<PROVEN|not-applicable>
```

Only positions 1 and 2 may emit it. GREEN requires all expected GitHub checks terminal
green on the current head/base, the allowed-file set exact, diff at most 400 lines, a clean
checkout, and a fresh critic acceptance. Position 1 additionally requires AST-equivalent
Python before/after and unchanged test names, assertions, literals, and collected-test
count. Any unavailable field is `MISCONFIGURED`, never GREEN.

Bootstrap run records use the existing frontmatter fields shown in the merged FQ-38 run
record plus the verdict, exact SHAs, checks, critic id, approval evidence, and expiry.
They are added as unique files under `docs/factory/runs/`; no absent README is required
to interpret them.

## Ordered recovery lane

1. **#41 — green baseline.** Format only the existing FQ-29 design and snapshot test.
   The owner approves the exact test patch in this implementation turn. GitHub CI must
   become green; no assertion, name, literal, collected test, or behavior changes.
2. **#40 — tracked authority.** Author a concise AGENTS/Claude adapter, contract, charter,
   and run schema directly from merged FQ-48 requirements. Workspace-only files are not
   copied or treated as approved sources. This is the sole policy-adoption exception.
3. **#42 — executable gates.** Track gate config/runner, isolated `pip-audit`
   provisioning, and parity tests. The candidate runner must pass deep gates from a clean
   checkout; its PR uses both green CI and the candidate verdict.
4. **New verifier item.** Track the negative-proof primitive and Claude/Codex verifier
   adapters with contract tests. It uses the merged deep runner.
5. **New implement item.** Track implement adapters and the narrowly approved
   load-bearing handoff rule. It uses deep gates and the merged verifier. Its merge expires
   the temporary recovery protocol.

Each position is one deterministic branch, Draft PR, fresh critic/verifier, and human
merge. Bootstrap positions never enter the generic ready queue. The continuing interactive
activator preclaims the deterministic branch while the item is waiting, then atomically
attaches the real protocol identity and moves only that item to `in-progress`. It may do
so only after the predecessor PR and merged-main CI are green. Closed-without-merge, stale
base, session loss, or SHA disagreement leaves every successor waiting.

No recovery claim occurs unless the live human-decision queue is at most two. The FQ-48
spec or one recovery PR may then occupy the third slot, but a later item cannot be claimed
until its predecessor leaves the queue.

## Corrected normal sequence

After position 5 merges, #39 becomes ready and proves enforcement previews and denied
capabilities with synthetic payloads. Then #43 adds immutable audit evidence, #44 adds
exact-head readiness and standard flow, #45 moves audit routines off product PRs, and #46
reconciles legacy PRs and activates enforcement only after live denial probes pass.

Existing #40, #41, and #42 are reordered; #39 and #43–#46 retain their safety outcomes.
Gate 4 atomically rewrites every recovery handoff to `wait-to-implement`, blocked by #48
protocol activation, with candidate blob ids only. After position 5 merges, the temporary
activator expires and #39 alone enters the normal ready queue. Queue snapshots remain
audit-only.

## Systems and external dependencies

- GitHub CI is the temporary oracle and must be green on exact head/base/app-bound checks.
- `pip-audit` is pinned Factory tooling isolated from Studio Baton's project environment
  and lock file. Provisioning failure makes deep gates `MISCONFIGURED`.
- FQ-48 supplies the temporary verdict, semantic proof, and run schema until tracked
  adapters exist.
- Fork credentials, branch rules, and audit protection remain preview-only until #46.

No learner data, product adapter, runtime dependency, release, real message, or real media
path is involved.

## Load-bearing scope

- #41: existing `tests/test_piece_snapshot.py` only for exact approved formatting.
- #40: `AGENTS.md`, `CLAUDE.md`, contract, charter, and run schema.
- #42: gate config/runner, isolated-tool bootstrap, and new parity tests.
- Verifier item: prove-test and verifier adapters plus new contract tests.
- Implement item: implement adapters, minimum contract change, and new contract tests.
- #39 and #43–#46 retain merged FQ-38 protected scope.

Every PR stays Draft and below 400 changed lines. Gate 3 must prove the budget for each
position, not estimate it. Approval of policy files never authorizes live GitHub settings.

## End-to-end flow and failure modes

Approve/merge FQ-48 → verify candidate blobs in the actual merge → generate the #41 patch
read-only in a temporary copy → owner approves that exact patch → preclaim #41 and attach
the merge SHA → #41 bootstrap verdict/CI/critic/merge → confirm merged-main green →
preclaim #40 → #40 bootstrap verdict/CI/critic/merge → preclaim #42 → candidate deep/CI/
verifier/merge → verifier item → implement item → expire recovery → #39 normal ready →
#43 → #44 → #45 → #46 activation.

A broad or stale exception is rejected by the exact issue allowlist, blob ids, order, SHA
checks, and expiry. Formatting deception is rejected by AST/literal/assertion/test-count
equivalence. Candidate self-grading is bounded by separate green CI and a cold critic.
Tool leakage is rejected by project lockfile/environment diffs. Concurrent queue edits are
reconciled as one complete state set; disagreement exposes zero ready items. If the
interactive session ends before a preclaim, fresh owner authorization is required.
