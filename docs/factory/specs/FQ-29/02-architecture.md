# Gate 2 — Architecture

## Oracle

This is a legacy-parity migration, but the legacy behavior is not the oracle
for every dimension.

The structural oracle is the `chunny_1` lesson-push behavior captured during
the parity audit: resolve the learner's assigned song, then place the song
title, original/source link, practice track, and sheet link before the lesson
summary. A synthetic song row and a golden block sequence will pin that shape;
no live learner row or document is needed.

The temporal oracle is the approved Gate 1 product invariant: after a lesson
is staged, later assignment changes must not alter that lesson. The legacy
pipeline's live lookup at push/send time is specifically the behavior being
corrected, so it cannot be the oracle for timing.

Before migration code changes behavior, new characterization tests will pin:

1. the legacy song-field mapping and output order with synthetic data;
2. the current Baton drift, where a changed assignment changes the later
   practice link; and
3. the approved behavior, where the staged lesson remains stable.

## Architectural decision

Capture the assigned song when `lesson stage` creates the lesson draft. Carry
that immutable value through contract generation, document publishing, the
published record, and message sending. No later step re-resolves the learner's
current song for that lesson.

Staging is the boundary because it is the first durable action for one lesson.
Capturing only at publish would still allow the summary contract to see a song
assigned after class. Capturing only at send is the current drift bug.

Re-staging intentionally creates a new draft and therefore takes a new
snapshot. That is the explicit correction path before publication.

## Existing systems and modules touched

### Learner and song database

The existing learner store already exposes the required reads:

- learner identity and `current_piece_id`;
- song title;
- original/source link;
- practice track; and
- sheet link.

SQLite, PostgREST, fallback, and fake stores already map these fields. No new
database table, column, query endpoint, or write is required.

If a learner has no assigned song, staging records a deliberate null snapshot.
If the learner names a song id that the store cannot resolve, staging refuses
with an existing typed error and does not save a misleading draft.

### Lesson staging and contract generation

The lesson draft gains one serializable song snapshot. Contract generation
reads that snapshot instead of querying the learner's current assignment.
The summary contract schema itself does not change: the song remains context
for the summarizer, not model-authored output.

### Document publishing

The publisher deterministically renders available snapshot fields before the
summary in this order:

1. song title;
2. original/source link;
3. practice track;
4. sheet link; and
5. validated lesson summary.

Missing optional links produce no placeholder block.

The existing preserve policy continues protecting recordings and manually
attached resources. On a forced republish, lesson-owned resource blocks are
deduplicated against preserved blocks by normalized identity (block type plus
URL, or block type plus text/icon). The replaceable title and summary are
rewritten, while an identical preserved bookmark, callout, or embed is not
appended twice.

Dry-run planning reports the song identity and the resource blocks that would
be appended without performing a write.

### Published lesson record

The local published record stores the same song snapshot alongside the frozen
short message. This remains the durable handoff between publish and send.

Older draft or published files without the new field remain readable. They are
treated as `snapshot unavailable`; Baton does not look up the learner's current
song to guess historical data. The practice-track field stays empty and the
normal send gate emits a warning or blocks if that studio configured the field
as required.

### Message sending

Message gathering reads the practice track only from the published song
snapshot. It may still read the learner's current instrument and the
document's current date, titles, and newest video because those fields are not
part of this product decision.

The messenger and recipient-selection layers do not change. Automated tests
continue to use the fake messenger; this feature never sends a real message as
part of verification.

### YouTube description

The YouTube description remains summary-based and is outside this change. It
does not currently display Song DB resources, so freezing them does not require
a media or YouTube API change.

## Data structures

The lesson draft and published record add this optional value:

```json
{
  "piece_snapshot": {
    "id": "piece-7",
    "title": "Fictional Study in C",
    "source_link": "https://example.invalid/source",
    "practice_track": "https://example.invalid/practice",
    "sheet_link": "https://example.invalid/sheet",
    "captured_at": "2026-08-25T00:00:00+00:00"
  }
}
```

`piece_snapshot` may be null only when no song was assigned at staging time or
when reading an older record written before this feature. URL fields may be
empty strings because they are optional; the id and title are required when a
snapshot is present.

No public network endpoint is added. Existing CLI commands keep their names
and arguments.

## End-to-end call flow

1. `lesson stage` resolves the learner.
2. It reads the learner's current song id once and resolves that song once.
3. It atomically saves the lesson draft with the snapshot and lesson notes.
4. `lesson contract` supplies the saved snapshot as summarizer context.
5. Ingest and render retain the draft unchanged.
6. `lesson publish --dry-run` plans snapshot resource blocks plus summary
   blocks; normal publish writes them in the approved order.
7. Publish saves the same snapshot in the per-session published record.
8. A later send reads that published snapshot, composes the practice link, and
   applies the existing fail-closed gate.
9. Reassigning the learner at any point after step 3 affects only future staged
   lessons.

## External dependencies

| Dependency | Required | Change |
| --- | --- | --- |
| Configured learner/song store | Yes, already required for staging | Read existing fields once; no schema change |
| Local atomic JSON state | Yes, already used for drafts and published records | Add one backward-compatible optional object |
| Configured document store | Yes for publishing | Append deterministic blocks through the existing interface |
| Messaging provider | Only for a real send | No adapter change; tests use the fake |
| New package or service | No | None |

## Load-bearing review

No load-bearing path needs modification.

In particular, the design avoids changes to:

- summary contracts;
- chat adapters;
- error and exit-code definitions;
- dependency manifests;
- workflows and factory policy; and
- existing test files.

Implementation will use new test files. If later design work discovers that a
load-bearing path or existing test must change, the specification stops and
returns to the owner before that work begins.

Because no load-bearing path is planned, a factory-critic pass is not required
at this gate.

## What could break elsewhere

- A draft staged before this feature has no snapshot. Sending it may lose a
  practice link that was previously guessed from the current assignment. This
  is intentional fail-closed behavior and must be explicit in CLI warnings.
- A profile with an invalid current song reference will fail earlier, during
  staging instead of contract or send. The remedy must tell the teacher to fix
  or clear the assignment.
- Forced republishing could duplicate preserved resource blocks unless the
  normalized-identity deduplication is covered by tests.
- Profiles customize preserve rules. Deduplication must work whether bookmark,
  embed, or callout blocks are preserved or replaceable.
- Staging files are user state. Deserialization must default a missing field
  safely and must not invalidate existing drafts.
- Published files are the send handoff. A partial publish must never combine a
  newly resolved song with an older short message; only the draft snapshot may
  be persisted.
- Changing `gather_context` must not stop it from reading the learner's current
  instrument or the document's current date, titles, and video.
