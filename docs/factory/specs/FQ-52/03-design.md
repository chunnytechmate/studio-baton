# FQ-52 — Evidence-boundary program design

## Exact records

```python
@dataclass(frozen=True)
class ProtocolIdentity:
    merge_sha: str
    blobs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Principal:
    login: Literal["chunnytechmate"]
    user_id: Literal[220607386]
    kind: Literal["User"]


@dataclass(frozen=True)
class PinnedComment:
    comment_id: int
    node_id: str
    url: str
    author: Principal
    marker: str


@dataclass(frozen=True)
class BootstrapIntent:
    issue: Literal[41]
    pull_request: int
    protocols: tuple[ProtocolIdentity, ProtocolIdentity]
    approved_base: str
    path_blobs: tuple[tuple[str, str], ...]
    patch_sha256: str
    semantic_proof_sha256: str
    expected_checks: tuple[tuple[str, Literal[15368]], ...]
    candidate: PinnedComment
    gate_status: Literal["pending-external"]


@dataclass(frozen=True)
class CheckIdentity:
    source_sha: str
    base_sha: str
    synthetic_merge_sha: str
    suite_id: int
    run_id: int
    checks: tuple[tuple[str, int, int, Literal["success"]], ...]


@dataclass(frozen=True)
class CriticEvidence:
    run_id: str
    source_sha: str
    base_sha: str
    merge_sha: str
    intent_blob: str
    diff_sha256: str
    ci: CheckIdentity
    verdict: Literal["accepted"]
    comment: PinnedComment
    body_sha256: str


@dataclass(frozen=True)
class RawStatus:
    status_id: int
    sha: str
    state: Literal["success", "error", "pending"]
    context: str
    description: str
    target_url: str
    creator: Principal


@dataclass(frozen=True)
class ValidatedAttestation:
    phase: Literal["spec-correction", "premerge", "closure"]
    comment: PinnedComment
    body_sha256: str
    status: RawStatus
```

The committed FQ-41 intent deliberately has no current source/merge/check ids, critic
verdict, or GREEN field. The committed FQ-52 spec run record likewise has no final-head
identity or ACCEPTED claim. Final-head-bound verdicts exist only as validated external
comment/status pairs.

## Contracts and call stack

```python
def verify_protocols(fq48: ProtocolIdentity, fq52: ProtocolIdentity) -> None: ...
def preflight_permissions(
    principal: Principal, stale_sha: str
) -> tuple[PinnedComment, RawStatus]: ...
def propose_and_prove_patch(base_sha: str) -> tuple[bytes, str, str]: ...
def create_placeholder(pr: int, principal: Principal) -> PinnedComment: ...
def write_intent(pr: int, comment: PinnedComment) -> BootstrapIntent: ...
def inventory_final_ci(pr: int, intent_blob: str) -> CheckIdentity: ...
def validate_critic(evidence: CriticEvidence, intent: BootstrapIntent) -> None: ...
def post_status_once(sha: str, context: str, body_sha256: str, url: str) -> RawStatus: ...
def reconcile_attestation(phase: str, pr_commits: tuple[str, ...]) -> ValidatedAttestation: ...
def derive_bootstrap_verdict(attestation: ValidatedAttestation, ci: CheckIdentity) -> str: ...
def verify_human_merge(pr: int, attestation: ValidatedAttestation) -> str: ...
def close_after_main_ci(issue: Literal[41], merge_sha: str) -> None: ...
```

FQ-52 adoption first commits all approved documents and the pending-only spec run record,
opens a Draft PR, binds its final head/base/synthetic merge and inherited CI result, runs
mypy on that fetched merge in writer and critic contexts, then posts a
`factory-spec-correction:v1` candidate. One raw status with stable context
`factory/spec-correction/fq-52`, state `error`, full body digest, owner creator, and candidate
URL derives `FACTORY_SPEC_CORRECTION: status=ACCEPTED`; the error state prevents it from
masquerading as green automation. The owner uses merge-commit mode.

After that merge, FQ-41 runs: verify both protocols; preserve and preflight; obtain exact
branch-deletion and refreshed-patch approvals; reclaim; format and commit; open Draft;
create placeholder; commit intent as final head; verify final CI; obtain bound cold critic;
update candidate; post the singleton premerge success status; fully reconcile; display the
derived GREEN line; await owner merge; verify merge provenance and main CI; emit/reconcile
the singleton closure pair; remove awaiting-review; close #41; permit only #40 preclaim.

## Exact repository paths and budgets

FQ-52 may change only its five spec files, `docs/factory/QUEUE.md`, the preserved
`docs/factory/runs/2026-08-25T154158Z-bootstrap-41.md`, and one new spec run record. The hard
total is 400 changed lines. No FQ-48 file, test, workflow, skill, gate, contract, charter,
or runtime file changes in the correction PR.

Resumed FQ-41 may change only `docs/factory/specs/FQ-29/03-design.md`,
`tests/test_piece_snapshot.py`, and one new pending-external bootstrap intent record. Its
hard total remains 70 changed lines; the existing-test approval remains exact and renewed.

## Required proofs and negative tests

- Correction: exact allowed paths/budget; six test jobs and leak job green; Ruff lint step
  green; format output identical to `main` and names only the two baseline files; writer and
  critic mypy green on the synthetic merge; wrong path/failure/creator/head or duplicate
  stable context rejects.
- Patch: exact target blobs/digest; identical full AST, function names, assertion/literal
  nodes, seven collected snapshot tests, and full-suite results before/after.
- Premerge: ignore provisional/historical suites; require exact eight app-15368 successes,
  intent blob, critic binding, singleton status, pinned body digest, current head/base/merge;
  missing, edited, duplicated, wrong-app, stale, or partial evidence rejects.
- Closure: require authorized owner merge-commit, exact parents and synthetic tree, current
  `main`, exact eight app-15368 push checks, and singleton closure pair; squash, rebase, bot,
  stale base, failed main, or repeat POST blocks #40.

## Three least-certain decisions

1. User-token status events expose creator identity rather than a GitHub App identity; the
   live preflight must prove the exact response fields before destructive recovery.
2. With no protection rules, provenance checks detect but cannot prevent a wrong merge;
   the owner must merge the attested Draft promptly using merge-commit mode.
3. An `error` status is intentionally used for an accepted docs-only correction so it can
   never look green; the human-facing explanation must make that unusual state unmistakable.
