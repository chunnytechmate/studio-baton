# FQ-48 — Recovery program design

## Records and contracts

```python
@dataclass(frozen=True)
class ProtocolIdentity:
    merge_sha: str
    blobs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PatchProposal:
    base_sha: str
    path_blobs: tuple[tuple[str, str], ...]
    patch_sha256: str


@dataclass(frozen=True)
class ProvenPatch:
    proposal: PatchProposal
    semantic: Literal["PROVEN"]
    proof_evidence: str


@dataclass(frozen=True)
class PatchApproval:
    proven: ProvenPatch
    owner_evidence: str
    approved_at: str


@dataclass(frozen=True)
class CheckIdentity:
    pull_request: int
    suite_id: int
    run_id: int
    source_sha: str
    base_sha: str
    merge_sha: str
    checks: tuple[tuple[str, int, int, str], ...]  # name, app id, check id, conclusion


@dataclass(frozen=True)
class BootstrapVerdict:
    position: Literal[1, 2]
    identity: CheckIdentity
    critic_evidence: str
    semantic: Literal["PROVEN", "not-applicable"]


def verify_protocol_identity(merge_sha: str, blobs: Mapping[str, str]) -> ProtocolIdentity: ...
def propose_format_patch(base_sha: str, paths: tuple[str, ...]) -> tuple[bytes, PatchProposal]: ...
def prove_semantic_equivalence(proposal: PatchProposal, patch: bytes) -> ProvenPatch: ...
def record_patch_approval(
    proven: ProvenPatch, owner_evidence: str, approved_at: str
) -> PatchApproval: ...
def inventory_bootstrap_ci(pr: int, expected: tuple[str, ...]) -> CheckIdentity: ...
def preclaim_bootstrap(
    issue: int,
    position: Literal[1, 2],
    base_sha: str,
    protocol: ProtocolIdentity,
    approval: PatchApproval | None,
) -> str: ...
def reconcile_activation(
    issue: int, branch_sha: str
) -> Literal["waiting", "partial", "in-progress", "inconsistent"]: ...
def emit_bootstrap_verdict(verdict: BootstrapVerdict) -> str: ...
def recover_bootstrap_session(issue: int, branch_sha: str, owner_authorization: str) -> str: ...
```

Validation rejects instead of returning partial values. Position 1 preclaim requires the
matching approved proven patch; position 2 requires `not-applicable`. The
preclaim return is the claimed 40-hex branch SHA.

`.factory/scripts/bootstrap-tools.sh --check|--install --version <pin>` exits 0 only
when isolated `pip-audit` is usable, exits 2 when unavailable, and never changes
`pyproject.toml`, `uv.lock`, or the project environment. The candidate
`.claude/scripts/gates.sh fast|full|deep` exits 0 GREEN, 1 RED, or 2 MISCONFIGURED and
always prints one final `FACTORY_GATES:` line.

## Identity and verdict

Gate 4 records candidate blob ids for `02-architecture.md`, this file, and
`04-slices.md`. Activation verifies those blobs in the actual FQ-48 merge tree.
Position 1 approval binds current `main`, both original file blobs, and SHA-256 of exact
`ruff format --diff` bytes; any changed byte/base/path/blob invalidates it.

The CI oracle is `.github/workflows/ci.yml`, app `github-actions@15368`, with exactly
eight latest-suite successes: `lint and types`; `no leaked paths, secrets, or personal
data`; tests on Ubuntu Python 3.10, 3.11, 3.12, 3.13, 3.14; and macOS Python 3.12.
`inventory_bootstrap_ci` selects one latest completed suite for the PR/source and ignores
historical suites; duplicates inside the selected suite reject. Its PR association must
bind the exact source/base, current PR head/base must still match, REST
`merge_commit_sha` must equal fetched `refs/pull/<pr>/merge`, and that commit's parents
and tree must be the bound base/head synthetic merge. Every check belongs to that suite,
has app id 15368, and succeeds.

```text
FACTORY_BOOTSTRAP: position=<1|2> status=GREEN source=<40-hex> base=<40-hex>
merge=<40-hex> suite=<id> run=<id> checks=<name@app:id,...>
critic=<evidence> semantic=<PROVEN|not-applicable>
```

Missing/ambiguous identity produces `status=MISCONFIGURED`, never GREEN. Run records use
`docs/factory/runs/<UTC>-bootstrap-<issue>.md`, copying the frontmatter of
`2026-08-25T085056Z-spec-38.md` and adding source/base/merge, protocol blobs, approval
digest, critic evidence, and the verbatim verdict.

## Exact files and budgets

Each allocation includes a 25-line run record and is a hard changed-line maximum:

| Item | Exact paths | Allocation |
|---|---|---:|
| #41 | `docs/factory/specs/FQ-29/03-design.md`; `tests/test_piece_snapshot.py`; run | 35+10+25=70 |
| #40 | `AGENTS.md`; `CLAUDE.md`; `docs/factory/CONTRACT.md`; `docs/factory/CHARTER.md`; `docs/factory/runs/README.md`; run | 15+55+105+135+35+25=370 |
| #42 | `.factory/gates.conf`; `.claude/scripts/gates.sh`; `.factory/scripts/bootstrap-tools.sh`; `tests/test_factory_gate_parity.py`; run | 12+200+35+115+25=387 |
| verifier | `.factory/scripts/prove-test.sh`; `.claude/skills/factory-verify/SKILL.md`; `.agents/skills/factory-verify/SKILL.md`; `tests/test_factory_verifier_protocol.py`; run | 110+80+15+135+25=365 |
| implement | `.claude/skills/factory-implement/SKILL.md`; `.agents/skills/factory-implement/SKILL.md`; `docs/factory/CONTRACT.md`; `tests/test_factory_implement_protocol.py`; run | 170+15+25+130+25=365 |
| #39 | `.factory/scripts/github_enforcement.py`; `tests/test_factory_github_enforcement.py`; `docs/factory/GITHUB.md`; run | 130+150+80+25=385 |
| #43 | `.factory/scripts/audit_record.py`; `tests/test_factory_audit.py`; `docs/factory/runs/README.md`; run | 130+170+50+25=375 |
| #44 | Claude/Codex implement+spec skills; `tests/test_factory_readiness.py`; `docs/factory/CONTRACT.md`; run | 30+120+10+12+140+35+25=372 |
| #45 | Claude triage+monitor skills+tune command; three Codex adapters; `tests/test_factory_audit_routines.py`; run | 85+70+60+36+105+25=381 |
| #46 | `.claude/commands/factory.md`; `.agents/skills/factory-status/SKILL.md`; `.factory/scripts/github_enforcement.py`; `.factory/scripts/doctor.sh`; `tests/test_factory_activation.py`; `docs/factory/GITHUB.md`; run | 55+12+40+55+110+75+25=372 |

Here `run` uses the pattern above. Skill paths are canonical
`.claude/skills/<name>/SKILL.md` and `.agents/skills/<name>/SKILL.md`; command paths
are canonical under `.claude/commands/`. Before edits, expanded paths and per-file
allocations are recorded; any expansion or actual total over 400 returns to spec.

## Proposal, activation, and recovery

`propose_format_patch` is read-only. In a temporary checkout it applies the proposed
bytes and proves identical Python AST without attributes, collected node ids, function
names, assertion/literal AST nodes, and full test result; the Markdown change is confined
to fenced Python formatting.

Main call stack: verify merged protocol → count live decisions (must be ≤2) → propose/prove
patch → obtain exact owner approval → preclaim branch while issue waits → write progress →
write real-SHA handoff → label `in-progress` → reconcile → implement → CI identity →
cold critic → verdict → run record → Draft PR → human merge → merged-main CI → successor.

Activation writes branch first, progress second, handoff third, label last, then rereads
all remote state. Recovery is idempotent:

| Observed state | Safe action |
|---|---|
| no branch | leave every issue waiting |
| branch only | matching authorized session may write progress |
| branch + progress | may write matching handoff |
| branch + progress + handoff, waiting | may set `in-progress` |
| exact complete `in-progress` | acknowledge and continue |
| duplicate/mismatched evidence | preserve branch/label, record failure if possible, stop |

Loss after the label write may therefore be complete-but-unacknowledged, not waiting.
Only a fresh owner-authorized activator matching branch SHA, protocol, approval, and
session may resume or restore a partial state. If rollback/comment writes fail, preserve
the reserved branch and current waiting/in-progress label, open no PR, claim no successor,
report the external-write failure, and stop. Bootstrap issues are never generic-ready.

## Tests and least-certain decisions

Gate tests cover format parity, isolated audit discovery, and missing-tool
`MISCONFIGURED`. Verifier tests reject base/head/proof/dirty/evidence drift. Implement
tests reject unapproved load-bearing work, expired bootstrap, and generic bootstrap claims.
FQ-38 enforcement/audit/readiness/routine/activation negative outcomes remain required.

Least certain: GitHub attaches checks to source while testing the merge ref, so both are
cryptographically bound; procedural verdicts rely on green GitHub CI plus a cold critic
until #42; concise authority/skill rewrites must preserve every safety rule, and omission
is a verifier rejection.
