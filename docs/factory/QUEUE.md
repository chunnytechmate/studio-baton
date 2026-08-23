# Factory queue snapshot

The operational queue lives in GitHub issue labels. This file is a reviewable snapshot
written by `factory-triage` and reported by `/factory`; implementation routines query
GitHub directly.

An unmerged update to this file must never block a later routine from seeing work. Durable
run evidence lives in one file per run under `docs/factory/runs/`.

**Dispositions**

| Disposition | Next stage |
|---|---|
| `ready-to-implement` | factory-implement picks it up |
| `ready-to-spec` | human runs factory-spec |
| `needs-info` | parked, question is on the issue |
| `wait-to-implement` | parked, blocker named below |
| `awaiting-review` | PR open, human owns it |
| `done` | merged by a human |

The corresponding live labels use the `factory:` prefix, for example
`factory:ready-to-implement` and `factory:awaiting-review`. The live issue also carries a
`factory-handoff:v1` comment with the fields needed by implementation.

---

## FQ-1: M6: version: true passes the config version gate because True == 1
- disposition: ready-to-implement
- source: https://github.com/chunnytechmate/studio-baton/issues/1
- last_triaged: 2026-08-23
- repro: confirmed
- files_expected: src/baton/core/config.py, tests/test_config_version_gate.py
- load_bearing: false
- gate_level: full
- done_when: `tests/test_config_version_gate.py` (new file — no existing test file modified) asserts that a profile whose `baton.yaml` says `version: true` (YAML boolean) raises `ConfigError` from `baton.core.config.load`, and that `version: 1` still loads identically; the boolean case fails on current `main` and passes after the fix
- confidence: high
- notes: Reproduced 2026-08-23 — `merged.get("version")` is `True` and `True != 1` is False in Python, so the boolean slips past the gate at `config.py:328-333`. Only existing coverage is `version: 99` rejection (`tests/test_config.py:103`); the fix must be a strict type check in `core/config.py` — do not touch `exits.py`/`errors.py`. `src/baton/core/**` is outside the charter's LOAD_BEARING globs. `version: 1.0` (float) shares the root cause and may be rejected in the same check per the issue body, but that latitude is optional — keep the diff tight.

---
