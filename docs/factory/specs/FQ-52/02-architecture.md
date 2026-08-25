# FQ-52 — Non-self-referential evidence architecture

## Authority, supersession, and oracle

FQ-48 made a repository run record both part of the final commit and a container for that
commit's SHA. Git commit identity includes the complete tree, so this demands an infeasible
cryptographic fixed point. CI also cannot attest an uncommitted tree.

After human approval and merge, FQ-52 supersedes only three FQ-48 requirements: storing the
current source/merge/check identities and GREEN verdict inside the committed bootstrap run
record; opening the Draft PR after that verdict; and treating that record as the final
verdict container. Every other FQ-48 invariant remains in force. FQ-41 activation must bind
both actual protocol merge SHAs and all approved architecture/design/slice blob ids.

GitHub remains the temporary oracle. A committed **intent record** contains stable inputs
but never its own commit SHA or a GREEN claim. A post-CI **attestation pair** is attached to
the already-final commit: a canonical pinned PR comment plus one raw GitHub commit-status
event. Neither changes the commit.

## Tamper-evident attestation pair

Before the final intent commit, the activator creates one placeholder PR comment and puts
its numeric id, node id, URL, author login/id, and marker in the intent record. After final
CI and cold criticism, that same comment is updated once to a canonical
`FACTORY_BOOTSTRAP_CANDIDATE:v1` payload. It does not claim GREEN.

The activator hashes the reread raw comment body and creates a success commit status on the
exact final source SHA. Pre-merge uses the stable case-normalized context
`factory/bootstrap/fq-41/premerge`; closure uses `factory/bootstrap/fq-41/closure`. The
full body digest is in the status description and the exact comment URL is its target.
Validation paginates raw status history, never combined status, and requires exactly one
event for the stable context across every PR commit for that phase. It binds status id,
SHA, state, context, description, target URL, creator login/id, and creation time, plus the
pinned comment identity, author, body, and body digest.

GitHub exposes create/list status operations but no status update/delete operation. An
edited or deleted comment breaks the status digest/URL; a replacement cannot match the
intent-pinned id; and a repeated status POST creates a second stable-context event and is
rejected. Only the validated candidate-plus-success-status pair derives the verbatim
`FACTORY_BOOTSTRAP ... status=GREEN` line. The PR body displays that derived line for the
human, but validation always recomputes it from the pair.

## One-shot adoption of the correction

FQ-52 itself cannot receive green repository CI before FQ-41 repairs the inherited format
baseline. Its docs-only Draft PR therefore uses one explicit
`FACTORY_SPEC_CORRECTION: status=ACCEPTED` verdict, never bootstrap GREEN. It is valid only
for the FQ-52 spec/status, queue snapshot, preserved blocked-run record, and one spec run
record; any other path rejects.

The correction verdict binds the final head/base/fetched synthetic merge, exact changed
paths and line budget, all six successful cross-version/platform test jobs, successful leak
job, successful Ruff lint step, and format failure containing only the two already-declared
baseline files with output equal to current `main`. Because workflow ordering skips mypy
after format fails, the writer and cold critic each run mypy independently on the fetched
synthetic merge and bind command, tool version, exit status, and output digest. They also
confirm the new FQ-52 documents format cleanly and introduce no new CI failure. A Draft PR,
cold critic acceptance, and explicit human merge remain mandatory. The correction verdict
expires at the FQ-52 merge and cannot authorize any implementation PR with red or pending
CI.

## Recovery and permission preflight

The correction PR preserves the existing blocked FQ-41 run-record content in `main` before
the stale branch is removed. After merge, the activator verifies the FQ-52 merge and blobs.
The previous patch approval is base-bound and therefore expires; the patch is regenerated,
semantically reproved, and explicitly reapproved even if its target blobs and digest match.

Before branch deletion or patch application, the same credential must prove issue-comment
and commit-status write authority. The principal is the owner user `chunnytechmate`, GitHub
id `220607386`; agent/bot identities reject. A uniquely marked durable comment on #41 proves
comment write/reread. A one-shot pending status on the exact stale branch SHA, under stable
context `factory/bootstrap/fq-41/preflight/<FQ-52-merge-prefix>`, proves status write and raw
history read. The response and reread must bind creator and principal. Missing scope,
ambiguous identity, duplicate context, or failed reread stops before destructive recovery.

Only then may exact-SHA owner authorization remove the stale remote reservation. A fresh
deterministic branch is claimed from corrected `main`. Progress, the handoff, and the label
are reconciled in that order. The handoff records FQ-48 and FQ-52 identities, fresh claim,
base, file blobs, approval, proof, and preflight ids while retaining links to the old
MISCONFIGURED evidence. No successor becomes ready.

## FQ-41 end-to-end flow

1. Apply and commit only the newly approved formatting patch.
2. Open a Draft PR as a CI container; its first run is provisional and never evidence.
3. Create the pinned placeholder comment. Commit the intent record naming the PR and
   placeholder, then push. That commit is the immutable final source head.
4. Select one latest completed GitHub Actions suite containing exactly the eight expected
   successful checks from app id 15368. Bind current PR source/base, fetched synthetic merge,
   its parents/tree, suite, run, and check ids. Any later commit rejects permanently and
   requires owner-authorized branch restart rather than re-attestation.
5. A fresh critic binds its accepted verdict to final source/base/merge, intent-record blob,
   complete diff, semantic proof, patch approval, and exact CI identity. Its canonical
   result is posted as a separate comment; the attestation binds that comment id and body
   digest.
6. Update the pinned candidate comment, create the pre-merge status once, then reread PR
   head/base, remote branch, merge ref, CI suite/checks, critic comment, candidate comment,
   and fully paginated raw statuses. Only a complete exact reconciliation derives GREEN.
7. Apply `factory:verified` to the PR and move #41 to `factory:awaiting-review`. The PR stays
   Draft until the owner reads it. Any drift removes verification, returns #41 to
   `needs-info`, and requires a fresh exact branch restart; no second pre-merge status is
   posted.
8. The owner must choose **Create a merge commit**. Squash and rebase are not valid for this
   bootstrap even though the repository currently permits all three and has no protection
   rules. Closure requires REST `merged_by` login/id to be the authorized owner, parents
   exactly `[attested base, attested source]`, tree equal to the attested synthetic merge,
   and `main` to advance from that base. Agent, bot, stale-base, squash, or rebase merges are
   `MISCONFIGURED`; they cannot be undone automatically and permanently block #40.
9. On that merge commit, require the exact eight-check successful `main` push suite from app
   15368. Create and reconcile the closure candidate/status pair using its separate stable
   context, binding the pre-merge status id/digest and actual merge provenance.
10. After closure succeeds, remove `factory:awaiting-review`, close #41, and permit only the
    already-approved #40 bootstrap activator to preclaim. #40 never enters generic ready.

## Partial-write and race recovery

- Placeholder created but intent absent: reread the exact placeholder, then commit intent.
- Intent pushed but CI incomplete: wait; never substitute provisional or historical runs.
- Candidate updated but status absent: query all raw statuses first; POST once only when the
  stable context is absent. An unknown POST result is recovered by GET, never blind retry.
- Status created but response/read failed: retry GET only. One exact event resumes; a
  mismatch or duplicate is permanent `MISCONFIGURED`.
- Post-status head/base/merge/check/comment drift: remove verification if present and require
  exact owner-authorized branch restart; never attest a second head in place.
- Human merge without valid shape/provenance: preserve evidence, block closure and #40, and
  request human recovery; never rewrite `main`.
- Closure comment/status partial writes follow the same query-before-create rule. #41 stays
  awaiting review or needs-info and #40 stays waiting until exact closure reconciliation.

## Dependencies and blast radius

GitHub Actions, issue comments, raw commit-status history, PR/merge APIs, and the existing
local Python toolchain are required. No dependency, workflow, rule, release, learner data,
runtime module, or live messaging path is added or changed. Execution still formats an
existing load-bearing test, so exact owner approval and Draft human review remain mandatory.
Without branch protection, an unauthorized merge cannot be prevented here; it is detected
and successors fail closed. Protection remains the later approved activation work.

Official API references:

- https://docs.github.com/en/rest/commits/statuses
- https://docs.github.com/en/rest/issues/comments
