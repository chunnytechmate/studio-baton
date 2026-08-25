# FQ-48 — Recovery program design

## Protocol records

```python
@dataclass(frozen=True)
class ProtocolIdentity:
    merge_sha: str
    blobs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PatchApproval:
    base_sha: str
    path_blobs: tuple[tuple[str, str], ...]
    patch_sha256: str
    approved_at: str


@dataclass(frozen=True)
class BootstrapVerdict:
    position: Literal[1, 2]
    source_sha: str
    merge_sha: str
    checks: tuple[tuple[str, int, int, str], ...]
    critic_evidence: str
    semantic: Literal["PROVEN", "not-applicable"]
```

The FQ-48 merge tree must contain the candidate blobs recorded at Gate 4 for
`02-architecture.md`, this file, and `04-slices.md`. Position 1 approval binds the
current `main` SHA, both original file blobs, and SHA-256 of exact `ruff format --diff`
bytes. Any later byte, base, path, or blob change invalidates approval.

Bootstrap run records copy the frontmatter schema from
`docs/factory/runs/2026-08-25T085056Z-spec-38.md`, then add `source_sha`, `base_sha`,
`merge_sha`, `protocol_blobs`, `approval_patch_sha256`, `critic_evidence`, and the
verbatim verdict. A concise critic finding is stored in the merged run file and PR body.

## Exact CI inventory

The temporary oracle is workflow `.github/workflows/ci.yml`, app
`github-actions@15368`, with exactly these successful check names on the current source
SHA: `lint and types`, `no leaked paths, secrets, or personal data`,
`test (py3.10 on ubuntu-latest)`, `test (py3.11 on ubuntu-latest)`,
`test (py3.12 on ubuntu-latest)`, `test (py3.13 on ubuntu-latest)`,
`test (py3.14 on ubuntu-latest)`, and `test (py3.12 on macos-latest)`.
The PR REST merge SHA and current base SHA are recorded before verdict. Missing, duplicate,
extra workflow jobs, changed app id/name, stale base, or non-success is
`FACTORY_BOOTSTRAP status=MISCONFIGURED` and returns to spec.

## Files and hard budgets

Changed-line budgets include a maximum 25-line run record and remain below 400:

| Position/item | Exact files | Maximum |
|---|---|---:|
| #41 baseline | `docs/factory/specs/FQ-29/03-design.md`, `tests/test_piece_snapshot.py`, run record | 80 |
| #40 authority | `AGENTS.md`, `CLAUDE.md`, contract, charter, `docs/factory/runs/README.md`, run record | 375 |
| #42 gates | `.factory/gates.conf`, `.claude/scripts/gates.sh`, `.factory/scripts/bootstrap-tools.sh`, `tests/test_factory_gate_parity.py`, run record | 390 |
| verifier item | `.factory/scripts/prove-test.sh`, Claude/Codex verify skills, `tests/test_factory_verifier_protocol.py`, run record | 370 |
| implement item | Claude/Codex implement skills, contract, `tests/test_factory_implement_protocol.py`, run record | 370 |
| #39 tracer | its current three declared files plus run record | 390 |
| #43 audit | its current three declared files plus run record | 390 |
| #44 readiness | its current declared skills/contract/test files plus run record | 390 |
| #45 routines | concise triage/monitor/tune adapters, new routine test, run record | 390 |
| #46 activation | concise status/doctor/enforcement/docs/test files, run record | 390 |

The remaining exact expansions are:

- verifier: `.factory/scripts/prove-test.sh`, `.claude/skills/factory-verify/SKILL.md`,
  `.agents/skills/factory-verify/SKILL.md`, `tests/test_factory_verifier_protocol.py`,
  run record;
- implement: `.claude/skills/factory-implement/SKILL.md`,
  `.agents/skills/factory-implement/SKILL.md`, contract,
  `tests/test_factory_implement_protocol.py`, run record;
- #39: `.factory/scripts/github_enforcement.py`,
  `tests/test_factory_github_enforcement.py`, `docs/factory/GITHUB.md`, run record;
- #43: `.factory/scripts/audit_record.py`, `tests/test_factory_audit.py`, runs README,
  run record;
- #44: `.claude/skills/factory-{implement,spec}/SKILL.md`,
  `.agents/skills/factory-{implement,spec}/SKILL.md`,
  `tests/test_factory_readiness.py`, contract, run record;
- #45: `.claude/skills/factory-{triage,monitor}/SKILL.md`,
  `.claude/commands/factory-tune.md`,
  `.agents/skills/factory-{triage,monitor,tune}/SKILL.md`,
  `tests/test_factory_audit_routines.py`, run record;
- #46: `.claude/commands/factory.md`, `.agents/skills/factory-status/SKILL.md`,
  enforcement script, doctor,
  `tests/test_factory_activation.py`, `docs/factory/GITHUB.md`, run record.

Before editing, each implementation writes a proposed per-file line allocation totaling at
most its row. Crossing either allocation or actual 400 changed lines returns to spec.
Workspace-only authority is not a source: #40 authors concise complete policy solely from
merged FQ-48 and the current product conventions.

Concrete allocations (path order follows the table) are: #41 `35+10+25=70`; #40
`15+55+105+135+35+25=370`; #42 `12+200+35+115+25=387`; verifier
`110+80+15+135+25=365`; implement `170+15+25+130+25=365`; #39
`130+150+80+25=385`; #43 `130+170+50+25=375`; #44
`30+120+10+12+140+35+25=372`; #45 `85+70+60+36+105+25=381`; and #46
`55+12+40+55+110+75+25=372`. The observed read-only Ruff patch for #41 is 45 changed
lines, below its 70-line allocation.

## Position 1 read-only proposal and proof

Run `ruff format --diff` for the two #41 paths and hash stdout without editing them.
Create a temporary checkout at the bound base, apply those exact bytes there, and prove:

- `ast.dump(ast.parse(...), include_attributes=False)` is identical for the Python test;
- collected node ids, function names, assertion/literal AST nodes, and full test result are
  identical;
- only Markdown fenced Python formatting changes in the design document;
- the generated patch matches the approved digest byte-for-byte.

Only then may the activator apply the patch to the claimed branch.

## Safe activation and write order

1. Confirm every recovery issue is waiting, open decision count is at most two, FQ-48 is
   merged, candidate blobs match its merge tree, and the interactive authorization is live.
2. Generate/prove the #41 proposal read-only and obtain exact owner approval.
3. Push the deterministic claim commit from the bound base while #41 is still waiting.
   A failed push leaves all GitHub state unchanged.
4. Write one progress comment with branch/source/base, protocol identity, patch approval,
   and session id; update the handoff with real merge SHA; then change only #41 to
   `in-progress`.
5. Re-read branch, comments, label, all successor labels, and review count. Any partial
   write moves #41 back to waiting if possible, records `activation-incomplete` on #48,
   opens no PR, and stops. No bootstrap issue is ever labeled ready.

Session loss before the branch push leaves all waiting. Loss after push resumes only in a
fresh owner-authorized interactive session that matches the immutable progress record and
remote SHA; otherwise the branch stays reserved and #41 stays waiting. Successor preclaims
repeat steps 1, 3–5 only after predecessor merge and merged-main CI green.

## Tests

- Gate parity tests prove unformatted Python/Markdown is red, formatted input is green,
  isolated `pip-audit` is found, and missing tooling is `MISCONFIGURED`.
- Verifier tests prove base/head drift, proof failure, dirty worktree, and evidence mismatch
  reject; an exact negative proof accepts.
- Implement tests prove only approved load-bearing handoffs pass, bootstrap expiry is
  irreversible, generic ready claims cannot consume bootstrap items, and old handoffs work.
- Existing FQ-38 synthetic enforcement, audit integrity, readiness, routine, and activation
  test outcomes remain unchanged.

## Three least-certain decisions

1. GitHub check attachment uses source SHA while Actions checks out the PR merge ref; Gate 3
   records both REST merge SHA and source/base rather than claiming they are the same.
2. The temporary verdict is intentionally procedural until #42; a cold critic and green
   app-bound CI are the independent oracles, not an untracked local script.
3. The authority and later adapter budgets require concise rewrites rather than copying
   local files; any loss of a current safety rule is a verifier rejection.
