# Factory run records

Write one immutable Markdown file per run; routines never append to a shared log. This
avoids merge conflicts between parallel routines and leaves structured evidence for
measuring the factory later. Filename, unique without coordination:

```
YYYY-MM-DDTHHMMSSZ-<stage>-<issue-or-run-id>.md
```

Start each file with this front matter:

```yaml
---
run_id: 2026-08-18T153012Z-implement-142
stage: triage | spec | implement | verify | monitor
started_at: 2026-08-18T15:30:12Z
finished_at: 2026-08-18T15:41:09Z
status: succeeded | stopped | rejected | infrastructure-failed | pending-external
issue: 142 | none
pull_request: 318 | none
gate_level: fast | full | deep | bootstrap | none
gate_status: GREEN | RED | MISCONFIGURED | pending-external | not-run
verifier: accepted | accepted-with-reservations | rejected | external-cold-critic-required | not-run
human_required: true | false
---
```

Record what was checked, changed, and why the run stopped — no secrets, transcripts, or
copied issue bodies; link instead. Bootstrap intents stay `pending-external`: the committed
record carries protocols, approval digests, and expectations, never its own final-head SHA,
CI ids, critic verdict, or GREEN claim — those bind only via the external comment/status
pair described in the contract. These records support simple measurements without a
service: queue age, gate failure rate, verifier rejection rate, time to review, and
escaped defects linked back to a run or pull request.
