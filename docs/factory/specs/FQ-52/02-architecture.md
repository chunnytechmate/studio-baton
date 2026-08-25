# FQ-52 — Non-self-referential evidence architecture

## Root cause and oracle

FQ-48 made a repository run record both a member of the final commit and a container for
that commit's SHA. Git commit identity includes the complete tree, so this demands a
cryptographic fixed point. Reordering commits cannot solve it, and CI cannot attest an
uncommitted tree.

GitHub remains the temporary external oracle. The corrected boundary separates:

1. a committed **intent record**, which contains stable inputs but never its own commit
   SHA or a GREEN claim; and
2. a post-CI **attestation**, which is attached to the already-final commit and therefore
   does not change it.

The attestation is a canonical PR comment plus a GitHub commit-status event on the exact
source SHA. The status context contains the SHA-256 of the exact comment body and links to
that comment. GitHub's API creates status events but provides no update or delete endpoint;
the verifier reads the uncombined status history and requires exactly one matching event.
Editing or deleting the comment breaks its digest or URL, while reposting the same context
creates a duplicate and fails closed. This uses GitHub's documented commit-status and issue
comment APIs, not repository settings or branch-rule mutation.

## Systems and records

The correction touches the Factory specification, the FQ-41 handoff, its deterministic
branch, one repository run record, GitHub pull-request comments, commit statuses, Actions
checks, and issue labels. It does not touch Studio Baton runtime modules.

The intent record carries the issue and PR numbers, protocol merge and blob identities,
approved base and target-file blobs, exact patch digest, semantic-proof digest, expected CI
check names and app id, attestation marker, and `gate_status: pending-external`. It omits
source, synthetic merge, suite, run, check ids, critic verdict, and GREEN status because
those facts do not exist until the final head is checked.

The pre-merge attestation carries the intent-record blob, final source and base SHAs,
fetched synthetic-merge SHA and parents, exact suite/run/check identities, critic evidence,
semantic result, patch digest, and the verbatim `FACTORY_BOOTSTRAP` GREEN verdict. Its
commit-status context is unique to the issue and full attestation-body digest.

A separate post-merge closure attestation binds the human merge commit, its parents, the
exact successful `main` CI suite, and the pre-merge attestation status id and digest. Only
that closure makes FQ-41 complete and permits #40 activation.

## End-to-end recovery flow

1. Human approves all FQ-52 gates and merges its docs-only correction PR. That PR makes no
   bootstrap GREEN claim. Its review must show the inherited baseline failure is still
   exactly the two declared formatting files, while every test, leak scan, and independent
   critic result succeeds.
2. The continuing owner-authorized session verifies the actual FQ-52 merge and spec blobs.
   Because the base changed, it regenerates and reproves the FQ-41 patch and obtains a new
   exact approval even if the target blobs and patch digest are unchanged.
3. The stale FQ-41 reservation is removed only with owner authorization bound to its exact
   remote SHA. A fresh deterministic branch is claimed from the corrected `main`; the issue
   moves from `needs-info` to `in-progress` only after remote evidence reconciles.
4. The approved formatting is applied and committed. A Draft PR is opened solely to create
   the CI container; it is explicitly not ready for review and its provisional run is not
   evidence.
5. The intent record, now able to name the PR, is committed and pushed. This is the final
   source head. Any later commit invalidates all following evidence.
6. One latest GitHub Actions suite must contain exactly the eight expected successful checks
   from app id 15368 and bind the final source, current base, and fetched synthetic merge.
7. A fresh critic reads the final diff and identities cold. Rejection, reservation drift,
   changed PR head/base, missing status permission, or any unavailable field stops
   `MISCONFIGURED` before attestation.
8. The activator posts one canonical attestation comment, rereads its exact body and URL,
   hashes the body, and creates one success commit status on the final source whose unique
   context contains that digest. It then rereads the raw status history and comment. Only
   an exact singleton match produces `FACTORY_BOOTSTRAP ... status=GREEN`.
9. The PR receives `factory:verified`, FQ-41 moves to `awaiting-review`, and the human owns
   the merge decision. Agents never merge.
10. After human merge, the activator verifies the exact successful `main` suite and emits
    the closure comment/status pair on the merge commit. It then marks FQ-41 complete and
    permits only #40 to enter its next approved bootstrap state.

## External dependencies

- GitHub Actions is required for the exact app-bound check suite already named by FQ-48.
- GitHub issue-comment APIs store the readable attestation payload. Comments are mutable,
  so their content is never trusted without the commit-status digest.
- GitHub commit-status APIs provide SHA-bound, append-only status events. A read/write
  permission probe against no live SHA is impossible, so implementation first inspects
  token permissions and stops before applying the patch if commit-status write authority
  is absent or ambiguous.
- No package, runtime dependency, release service, learner data, or live messaging system
  is added.

## Load-bearing scope and blast radius

Execution still modifies the existing snapshot test, so the exact owner approval and Draft
human read remain mandatory. The correction governs load-bearing Factory behavior even
though its specification files are documentation. It does not authorize edits to the
charter, contract, workflow, gates, skills, repository settings, or branch protection.

The main risks are stale PR identity, duplicate or forged status contexts, an edited
attestation comment, insufficient token permission, a provisional CI run mistaken for the
final run, and a correction PR that introduces a new failure. Exact SHAs, full body digest,
raw status-history cardinality, app id, suite membership, final-head rereads, and inherited-
failure comparison make each case fail closed. The known docs-only correction exception
expires when FQ-52 merges; it cannot authorize any implementation PR with red or pending CI.

Official API references:

- https://docs.github.com/en/rest/commits/statuses
- https://docs.github.com/en/rest/issues/comments
