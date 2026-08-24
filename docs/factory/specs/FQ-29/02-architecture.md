# Gate 2 — Architecture

## Oracle and decision

The structural oracle is the audited `chunny_1` lesson-push order: song title,
source link, practice track, sheet link, then summary. New synthetic golden tests
pin the mapping and block shapes. The temporal oracle is approved Gate 1: after
staging, a later assignment change cannot alter that lesson. Legacy live lookup
at publish/send time is the bug, not an oracle.

Capture the assigned song when `lesson stage` creates its durable draft. Carry
that immutable snapshot through contract, render, document publish, published
record, and send. Re-staging refreshes it before publication.

## Systems touched

- Existing learner stores supply `current_piece_id` and one `get_piece` read.
  SQLite, PostgREST, fallback, and fake mappings already expose id, title,
  source, practice, and sheet fields; there is no DB schema/write change.
- `LessonDraft` and `PublishedRecord` persist the snapshot in atomic JSON.
- Lesson CLI captures it, supplies frozen summarizer context, renders it, and
  prevents unsafe forced republishing.
- `SummaryPublisher` prepends resources through the existing `DocStore` API.
- Send gathering reads the published practice track, while learner instrument
  and document date/title/video remain live as today.
- Contract schema, YouTube/media, recipient selection, and messengers do not
  change. Tests use fakes and never contact a person or real learner profile.

## Data shape and compatibility

```json
{"piece_snapshot":{"status":"captured","captured_at":"2026-08-25T00:00:00+00:00","piece":{"id":"piece-7","title":"Fictional Study in C","source_link":"https://example.invalid/source","practice_track":"https://example.invalid/practice","sheet_link":"https://example.invalid/sheet"}}}
```

Status is `captured`, `none`, or `unavailable`. `captured` requires timestamp,
id, and title; links may be empty. `none` is a deliberate no-assignment observed
at staging, with timestamp and null piece. A missing key on legacy state becomes
`unavailable`, with empty timestamp and null piece. Malformed state fails closed.

Contract context keeps the compatible name `current_piece` but sources it from
the snapshot and removes live `current_piece_id` from the learner dictionary.
Older records never guess from the current assignment: practice track is empty,
so the existing configurable send gate warns or blocks.

## Document behavior

Generated blocks are fixed and ordered before summary blocks:

1. `heading_2`, text `🎵 <title>`;
2. source `bookmark` URL, if present;
3. `callout`, icon `🎧`, text `Practice track: <URL>`, if present;
4. sheet `embed` URL, if present.

Preserve policy still owns deletion safety. Baton never deletes an arbitrary
preserved block. Exact resource identity after edge trimming is `(bookmark,
URL)`, `(callout, exact text, icon)`, or `(embed, URL)`; URL case/query/fragment
remain significant. The replaceable heading has no resource identity.

Before any `--force` plan or write against an existing published record, compare
snapshot status and all song fields, ignoring only `captured_at`. Changed or
legacy-unknown snapshots are refused because old preserved links cannot be
safely attributed. For the same snapshot, exact resources already preserved are
skipped, missing ones are appended, and replaceable heading/summary are rewritten.
Dry run reports snapshot status/id and resource counts without writing.

## Call flow

1. Stage resolves learner/session, reads the assigned song zero or one time,
   rejects a dangling id, and atomically saves notes plus snapshot.
2. Contract reads only the draft snapshot; ingest retains the draft.
3. Render shows frozen song resources plus validated summary.
4. Publish loads any existing record, runs the force guard, plans/writes snapshot
   blocks and summary, and stores the same snapshot in the published record.
5. Send reads that published snapshot and applies the unchanged fail-closed gate.
6. Reassignment after step 1 affects only future staged lessons.

## Dependencies and load-bearing review

No new runtime package, service, endpoint, adapter method, schema, or migration.
Required existing dependencies are learner store, local JSON state, and document
store; a real messenger is needed only outside tests.

No load-bearing source path changes. One existing test is load-bearing:
`tests/test_send.py` asserts the obsolete live-song lookup. On 2026-08-25 the
owner approved replacing only that assertion and its fixture support. The PR
stays Draft, needs human read, and uses `deep` gates. No other existing test,
gate assertion, dependency manifest, factory policy, or workflow may change.

## Critic pass and response

Fresh critic result:

```text
position: concerns
strongest_objection: Preserved blocks have no ownership provenance, so normalized identity only prevents exact duplicates; re-staging a published session with a different snapshot and using --force can leave old preserved Baton links beside new links.
assumptions_introduced: staging may capture a pre-correction assignment; live current_piece_id could conflict with frozen context; null and missing legacy state were conflated; exact shapes/URL normalization were unspecified; existing-test approval is narrow.
maintainability_cost: snapshot interpretation spans staging, contract, publishing, and sending while resource ownership remains implicit.
simpler_alternative: reject --force when draft and published snapshots differ; distinguish deliberate no-song from legacy unavailable.
would_a_stranger_understand: no
```

Response: adopt that simpler alternative; define the three states, fixed block
shapes, exact identities, and removal of live id above. Ownership remains an
explicit limitation and is retested at the program-design gate.

## Breakage risks

- Staging before an assignment correction freezes the wrong song; re-stage is
  the correction path only before first publish.
- Dangling song ids now fail earlier at stage with a fix/clear remedy.
- A changed or unknown forced republish requires human inspection/cleanup.
- Preserved manual/recording blocks stay untouched even when visually similar.
- Legacy sends may lose a guessed practice link; that is deliberate fail-closed
  behavior, not automatic data repair.
- Append-before-delete and current session-completion recovery must remain intact.
