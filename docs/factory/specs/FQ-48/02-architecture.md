# FQ-48 — Bootstrap recovery architecture

## Oracle

The recovery has three trust stages rather than pretending the missing Factory can verify
itself.

1. The baseline-repair PR is judged by GitHub CI on its exact synthetic merge commit, an
   assertion-preserving diff check, and a fresh verifier. It must make both the PR and
   merged `main` green before policy bootstrap begins.
2. The authority-adoption PR is the only policy bootstrap exception. It is docs-only,
   Draft, restricted to approved authority files, checked by green CI and a fresh critic,
   and merged only by the owner.
3. Once the tracked authority exists, candidate gate tooling is introduced and exercised
   in its own PR. After that PR merges, every remaining load-bearing slice must produce a
   real deep `FACTORY_GATES` verdict; missing tooling is `MISCONFIGURED`.

No PR after baseline recovery may rely on an inherited red check.

## Bounded bootstrap lane

The corrected queue begins with four ordered items:

1. **#41 — green baseline.** Formatting-only changes to the existing FQ-29 design and
   existing snapshot test. The owner must explicitly approve the existing-test edit in
   the implementation session. No assertion, value, test name, or product behavior may
   change.
2. **#40 — tracked authority.** Adopt the approved AGENTS/Claude adapters, contract, and
   charter. The contract records the bounded bootstrap lane and its exact issue allowlist.
3. **#42 — executable gates.** Track the gate runner/config and isolated deep-tool
   provisioning. Candidate deep gates must run successfully before this PR can be merged.
4. **New workflow item — load-bearing execution.** Track the implement adapters and make an
   explicitly approved load-bearing handoff legal only when deep gates and independent
   verification succeed. Merging it expires the bootstrap exception.

These four items use one issue, one deterministic branch, one Draft PR, one fresh verifier,
and one human merge each. Only #41 is initially claimable. A handoff carries:

```text
delivery: bootstrap
bootstrap_spec: FQ-48
bootstrap_position: <1..4>
blocked_by: <predecessor or none>
owner_approval: Gate 4 approval date
```

For positions 1–3, the current generic `factory-implement` routine is not used because it
would correctly reject the load-bearing work. They run only in the same interactive,
owner-approved bootstrap workflow defined by the tracked contract. The allowlist is exact,
cannot accept new issue numbers, and expires when position 4 merges. Any disagreement among
label, handoff, issue number, predecessor merge, branch, or approval date fails closed.

## Corrected normal sequence

After the bootstrap lane:

1. #39 proves enforcement previews and denied-capability probes with synthetic payloads.
2. #43 adds immutable audit evidence.
3. #44 adds exact-head progress/readiness and the standard feature flow.
4. #45 moves audit-only routines off the product merge queue.
5. #46 reconciles legacy PRs, proves real permission denial, and activates enforcement.

#39 remains `wait-to-implement` until the workflow item is merged. Existing #40, #41, and
#42 are reordered rather than duplicated. Their live handoffs are rewritten only after
Gate 4 approval. Obsolete dependencies are replaced atomically; no two items are ready.

## Systems touched

- GitHub issue labels and canonical handoff comments for #39–#46 and the new workflow item.
- Tracked repository authority: AGENTS/Claude adapters, contract, and charter.
- Local gate runner/config plus isolated security-audit tooling.
- Factory implement adapters, later readiness/audit/control-room components from FQ-38.
- The two known formatting-baseline files in #41 only.

GitHub rules, fork credentials, audit-branch protection, and strict required contexts remain
preview-only until #46. No learner profile, product adapter, runtime dependency, release,
real message, or real media path is involved.

## External dependencies

- `pip-audit` is an exactly recorded Factory execution tool, isolated from Studio Baton's
  project environment and lock file. Provisioning failure makes deep gates
  `MISCONFIGURED`; it never becomes a project dependency.
- GitHub Actions is the baseline oracle and must be green on the exact current head/base.
- Fork and credential setup remains deferred to activation and stays owner-controlled.

## Load-bearing scope

- #41: existing `tests/test_piece_snapshot.py` (format only, same-session approval).
- #40: `AGENTS.md`, `CLAUDE.md`, `docs/factory/CONTRACT.md`, and
  `docs/factory/CHARTER.md`.
- #42: `.factory/gates.conf`, `.claude/scripts/gates.sh`, isolated-tool bootstrap, and
  new gate-parity tests.
- Workflow item: canonical and Codex implement skills plus the minimum contract amendment.
- #39 and #43–#46 retain the approved FQ-38 protected scope.

Every PR stays below 400 changed lines and Draft. Existing-test approval for #41 does not
authorize assertion changes. Policy approval does not authorize live repository mutation.

## End-to-end call flow

Gate 4 approval → rewrite handoffs with #41 ready → owner approves the exact existing-test
format edit → #41 CI/verifier/merge → confirm merged-main green → #40 authority
CI/critic/merge → #42 provision candidate tooling and pass candidate deep gates → merge →
workflow item passes tracked deep gates and fresh verification → merge and expire bootstrap
lane → #39 becomes ready → #39/#43/#44/#45 merge in order → #46 applies only explicitly
approved settings and proves denied permissions → enforcement becomes active.

Every predecessor is checked by merged PR state and merged-main SHA, not merely a closed
issue or label. A failed or closed-without-merge predecessor leaves successors waiting.

## What could break

- A broad bootstrap exception could become a permanent bypass. The exact issue allowlist,
  ordered predecessors, expiry, and Draft/human merge requirements prevent reuse.
- Mechanical formatting could hide a test change. Token-level assertion/name/value checks
  and a cold verifier make any semantic change a rejection.
- Candidate gates could grade themselves too generously. Green GitHub CI and an independent
  critic remain separate oracles until the runner is merged.
- A tool install could alter project dependencies. Isolation and lockfile-diff rejection
  prevent that.
- Concurrent label edits could expose two ready items. Queue reconciliation verifies the
  complete state set after every write and fails closed on disagreement.
- Authority files could exceed the review budget. The authority slice uses the already
  approved local sources and compact run evidence; exceeding 400 lines returns to Gate 4
  rather than silently splitting authority.
