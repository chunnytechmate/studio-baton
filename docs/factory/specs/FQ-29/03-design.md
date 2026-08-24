# Gate 3 — Program design

## Files

New: `src/baton/render/piece.py`, `tests/test_piece_snapshot.py`,
`tests/test_piece_publish.py`, `tests/test_lesson_piece_flow.py`.

Modified: `src/baton/pipelines/staging.py`, `src/baton/cli/cmd_lesson.py`,
`src/baton/pipelines/publish.py`, `src/baton/pipelines/send.py`, `README.md`, and
`docs/notion-setup.md`. Also modify only the owner-approved fixture/assertion in
`tests/test_send.py`; unrelated send gates remain untouched. No protected source,
adapter interface, schema, dependency, workflow, or factory-policy change.

## Signatures and contracts (no implementations)

`src/baton/pipelines/staging.py`:

```python
PieceSnapshotStatus = Literal["captured", "none", "unavailable"]
@dataclass(frozen=True)
class PieceSnapshot:
    status: PieceSnapshotStatus
    captured_at: str = ""
    piece: Piece | None = None
    @classmethod
    def capture(cls, piece: Piece | None) -> PieceSnapshot: ...
    @classmethod
    def unavailable(cls) -> PieceSnapshot: ...
    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> PieceSnapshot: ...
    def to_dict(self) -> dict[str, Any]: ...
    def same_content(self, other: PieceSnapshot) -> bool: ...
```

`capture` timestamps captured/none state. Missing record key alone maps to
unavailable; malformed state raises existing `UsageError` with re-stage remedy.
`same_content` ignores timestamp only. `LessonDraft` gains
`piece_snapshot: PieceSnapshot` defaulting to unavailable and always serializes
it. `PublishedRecord.save` writes the same object.

`src/baton/render/piece.py`:

```python
ResourceIdentity = tuple[str, str, str]
def to_blocks(snapshot: PieceSnapshot) -> list[dict[str, Any]]: ...
def to_markdown(snapshot: PieceSnapshot) -> str: ...
def payload_identity(block: Mapping[str, Any]) -> ResourceIdentity | None: ...
def stored_identity(block: Block) -> ResourceIdentity | None: ...
```

Captured state renders the four Gate-2 shapes/order; blank links omit their own
block; other states render nothing. Identity covers resources, never heading.

`src/baton/pipelines/publish.py`:

```python
def _without_preserved_resource_duplicates(
    generated: list[dict[str, Any]], preserved: list[Block]
) -> list[dict[str, Any]]: ...
class SummaryPublisher:
    def plan(self, doc_id: str, summary: dict[str, Any], *,
             piece_snapshot: PieceSnapshot | None = None,
             callout_texts: dict[str, str] | None = None) -> dict[str, Any]: ...
    def publish(self, doc_id: str, summary: dict[str, Any], *,
                piece_snapshot: PieceSnapshot | None = None,
                callout_texts: dict[str, str] | None = None,
                replace: bool = True) -> PublishResult: ...
```

`None` preserves summary-only callers. Plan/publish share assembly: song then
summary. Only exact identities in the preserved partition are skipped; deletion
policy and append-before-delete remain unchanged. Plan adds snapshot status/id
and resource count.

`src/baton/cli/cmd_lesson.py`:

```python
def _capture_piece_snapshot(store: LearnerStore, learner: Learner) -> PieceSnapshot: ...
def _require_force_compatible(draft: LessonDraft,
    published: Mapping[str, Any] | None, *, force: bool) -> None: ...
```

Capture makes at most one piece read; a dangling id stops before save. Contract
uses frozen piece and omits live id. Block/Markdown render include song; message
render does not duplicate it. Force mismatch raises `UsageError` before plan,
document mutation, completion, YouTube, or record write. Dry-run and publish pass
the same snapshot.

`src/baton/pipelines/send.py`: keep `gather_context` signature. It uses only
`PieceSnapshot.from_record(published)` for practice track, records status in
`SendContext.extra`, and still reads current learner instrument/name. Empty
none/unavailable values flow through existing configured gate; remedy points to
stage/republish, not current assignment.

## Main stack

```text
lesson stage -> handle_stage -> capture -> LearnerStore -> StagingStore.save
lesson contract -> StagingStore.require -> frozen context -> report
lesson render -> piece renderer -> summary renderer -> report
lesson publish -> PublishedRecord.get -> force guard -> SummaryPublisher
  -> PreservePolicy -> piece+summary render -> append -> delete replaceable
  -> PublishedRecord.save -> existing completion flow
send -> PublishedRecord -> gather_context -> gate -> compose -> Messenger
```

## Tests and proof

Only fictional data and `.invalid` URLs; no real transcript/video/learner/page or
message. Each slice first records a focused new red test via `prove-test.sh`.

- `test_piece_snapshot.py`: all three states/round trips; missing vs malformed;
  comparison ignores time but detects every field; no-song/one-read/dangling-id
  stage behavior.
- `test_piece_publish.py`: golden four-block shape/order; optional omission;
  none/unavailable; dry-run parity; same-snapshot exact dedup/restoration;
  identity differences; manual preservation; mismatch rejected before writes.
- `test_lesson_piece_flow.py`: stage A then assign B still contracts, renders,
  publishes, records, and sends A; re-stage before publish refreshes B; no live id
  leaks into contract; legacy send never falls back and gate warns/blocks.
- Approved `test_send.py` change: add snapshot fixture, make live assignment a
  different fictional song, replace only the live-track test with snapshot-wins.
- Existing `test_staging.py`, `test_lesson_cli.py`, `test_publish.py`,
  `test_docs.py`, contract, and chat suites remain green unchanged.
- Final Draft PR requires `deep`; install `pip-audit` only in local `.venv`
  because the charter says it is missing. No manifest/lock edit.

## Three least-confident decisions

1. Staging is the best durable boundary but can freeze a teacher's pre-correction
   assignment; re-stage fixes it only before publication.
2. Block ownership stays implicit because `append_blocks` returns no ids. Exact
   dedup plus mismatch refusal is safe but makes corrections less convenient.
3. Legacy unavailable records may now block required-track sends. This is more
   truthful than guessing the learner's newer assignment, but adds cleanup work.
