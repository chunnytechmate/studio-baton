# FQ-38 — Program design

## Repository adoption and exact paths

The authoritative factory is currently local-only: `origin/main` contains queue/spec/run
evidence but not `AGENTS.md`, `CLAUDE.md`, the contract, charter, gate runner, or skills.
Migration therefore adopts approved local sources into Git before changing their behavior.

Core authority, new to `origin/main`:

- `AGENTS.md`, `CLAUDE.md`
- `docs/factory/CONTRACT.md`, `docs/factory/CHARTER.md`

Baseline and deterministic enforcement:

- existing `docs/factory/specs/FQ-29/03-design.md`
- existing `tests/test_piece_snapshot.py` (format only; same-session owner approval required)
- new-to-main `.factory/gates.conf`, `.claude/scripts/gates.sh`, `.factory/scripts/doctor.sh`
- new `.factory/scripts/github_enforcement.py`
- new `tests/test_factory_gate_parity.py`, `tests/test_factory_github_enforcement.py`
- new-to-main `docs/factory/GITHUB.md`

Workflow and evidence, new-to-main then modified:

- `.claude/skills/factory-{implement,spec,triage,monitor}/SKILL.md`
- `.claude/commands/factory.md`, `.claude/commands/factory-tune.md`
- matching `.agents/skills/factory-{implement,spec,triage,monitor,status,tune}/SKILL.md`
- new `.factory/scripts/audit_record.py`
- new `tests/test_factory_readiness.py`, `tests/test_factory_audit.py`
- new-to-main `docs/factory/runs/README.md`; modified contract/charter from core adoption

Hooks, settings, verifier/prove-test internals, CI workflow, product code, dependency locks,
and real-data adapters remain outside FQ-38 unless a slice fails closed and returns to spec.

## Data and command contracts

```python
@dataclass(frozen=True)
class CheckContext:
    name: str
    app_id: int

@dataclass(frozen=True)
class EnforcementInventory:
    source_sha: str
    merge_sha: str
    contexts: tuple[CheckContext, ...]

def inventory_green_checks(pr: int) -> EnforcementInventory: ...
def preview_bootstrap_rules(repo: str) -> dict[str, object]: ...
def preview_strict_rules(repo: str, inventory: EnforcementInventory) -> dict[str, object]: ...
def probe_permissions(upstream: str, fork: str) -> dict[str, bool]: ...
```

Rule previews are pure JSON output. Applying either payload requires explicit `--apply`,
records before/after JSON, disables auto-merge, and fails unless names/app ids were observed.

```python
@dataclass(frozen=True)
class ProgressRecord:
    run_id: str
    branch: str
    source_sha: str
    base_sha: str
    checkpoints: tuple[tuple[str, str], ...]
    gate_failures: int
    verifier_rejections: int

@dataclass(frozen=True)
class ReadinessRecord:
    issue: int
    pull_request: int
    source_sha: str
    base_sha: str
    merge_sha: str
    check_runs: tuple[str, ...]
    gate_line: str
    proof_line: str
    verifier: str
    human_read: bool

def validate_resume(progress: ProgressRecord, remote_sha: str, label: str) -> None: ...
def validate_audit_ref(ref: str) -> None: ...
def append_record(path: str, content: bytes, expected_tip: str) -> str: ...
```

Progress comments must be authored by the coordinator identity, have one canonical marker,
a deterministic fork branch, full 40-character SHAs, unique checkpoint ids, and known
states. They are mutable operational state. Readiness/run files on `factory-audit` are
authoritative first-introduction blobs; tip mutation/deletion makes validation fail.

## Main call flow

Spec approval → fork claim → progress comment → checkpoint commits → local gates → Draft
cross-fork PR → strict CI inventory → fresh exact-head verifier → append readiness record →
idempotent verified/awaiting labels → one owner decision. Resume starts at progress parsing
and remote-SHA equality; it never creates or force-pushes a replacement claim.

## Tests and proof

- Unformatted Python/Markdown code blocks make the local lint judgment red; formatted input
  passes and the summary schema stays compatible.
- Missing `pip-audit` makes deep `MISCONFIGURED`; provisioned tooling runs the audit.
- Bootstrap rules have zero required contexts; strict rules use exact app-bound contexts,
  strict freshness, PR-only/no-force/no-delete, and auto-merge off.
- Permission probes prove builder/coordinator cannot upstream merge, arm auto-merge, push
  `main`, alter rules, or rewrite protected audit history.
- Readiness refuses red/pending/missing checks, stale base, wrong SHA, rejected verifier,
  malformed progress, unauthorized comment author, and changed-head evidence.
- A second same gate failure or verifier rejection records the named blocked state.
- Resume accepts only the existing in-progress issue, canonical branch, matching remote SHA,
  and valid counters/checkpoints.
- Audit append survives a non-fast-forward retry; mutation/deletion of an introduced blob
  makes integrity `MISCONFIGURED` and the original remains recoverable.
- Control-room count includes accepted human-read Drafts, rejected PRs, and named
  `needs-info`; it excludes CI-pending Drafts and audit activity.
- Standard specs require one packet approval; high-risk/load-bearing specs retain four.
- Baseline formatting changes no assertion/behavior; the complete suite stays green.

All new tests use synthetic GitHub payloads, temporary Git repositories, fake identities,
and `.invalid` values. No live permission mutation occurs in tests.

## Three least-certain decisions

1. GitHub plan/account support for an owner-controlled fork and app-bound strict contexts
   must be proven read-only before setup; unsupported capability leaves enforcement partial.
2. Adopting the local factory into Git requires several sub-400-line bootstrap PRs, a
   one-time merge cost that cannot honestly be removed by the migration itself.
3. First-introduction audit validation is strong but adds history-walk cost; tests must set
   a bounded failure mode rather than silently trusting the branch tip.
