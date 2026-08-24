# Gate 3 — Program design

## Files

New: `src/baton/render/piece.py` and three `tests/test_piece_*.py` flow files.
Modified: staging, lesson CLI, publish, send, README, Notion setup, plus only the
approved `tests/test_send.py` fixture/assertion. Exact paths are in Gate 4; no
protected source, schema, dependency, workflow, adapter, or unrelated test edit.

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

Capture timestamps captured/none; only missing maps to unavailable; malformed
state raises `UsageError`; equality ignores time. Draft and published record
serialize the same snapshot.

`src/baton/render/piece.py`:

```python
ResourceIdentity = tuple[str, str, str]
def to_blocks(snapshot: PieceSnapshot) -> list[dict[str, Any]]: ...
def to_markdown(snapshot: PieceSnapshot) -> str: ...
def payload_identity(block: Mapping[str, Any]) -> ResourceIdentity | None: ...
def stored_identity(block: Block) -> ResourceIdentity | None: ...
```

Captured renders Gate-2 shapes/order; blank links omit their block; other states
render nothing. Identity covers resources, never heading.

`src/baton/pipelines/publish.py`:

```python
def _without_preserved_resource_duplicates(generated: list[dict[str, Any]],
    preserved: list[Block]) -> list[dict[str, Any]]: ...
class SummaryPublisher:
    def plan(self, doc_id: str, summary: dict[str, Any], *,
        piece_snapshot: PieceSnapshot | None = None,
        callout_texts: dict[str, str] | None = None) -> dict[str, Any]: ...
    def publish(self, doc_id: str, summary: dict[str, Any], *,
        piece_snapshot: PieceSnapshot | None = None,
        callout_texts: dict[str, str] | None = None,
        replace: bool = True) -> PublishResult: ...
```

`None` retains summary-only behavior. Both methods assemble song then summary,
skip only exact preserved identities, retain append-before-delete, and plan adds
snapshot status/id/resource count.

`src/baton/cli/cmd_lesson.py`:

```python
def _capture_piece_snapshot(store: LearnerStore, learner: Learner) -> PieceSnapshot: ...
def _require_force_compatible(draft: LessonDraft,
    published: Mapping[str, Any] | None, *, force: bool) -> None: ...
```

Capture reads at most one piece; dangling id stops before save. Contract uses
frozen piece and omits live id. Render includes song. Force mismatch stops before
plan or any write; dry-run and publish pass the same snapshot.

`src/baton/pipelines/send.py`: keep `gather_context` signature; practice comes
only from `PieceSnapshot.from_record(published)`, status goes in `extra`, and
current learner lookup remains only for name/instrument. Existing gate handles
empty none/unavailable; remedy points to stage/republish.

## Main stack

```text
stage/contract -> capture -> StagingStore -> frozen context
render -> piece renderer -> summary renderer
publish -> PublishedRecord.get -> force guard -> SummaryPublisher
  -> PreservePolicy -> render -> append -> delete -> PublishedRecord.save
send -> PublishedRecord -> gather_context -> gate -> compose -> Messenger
```

## Tests and proof

Fictional `.invalid` data only; each slice records red proof with `prove-test.sh`.

- `test_piece_snapshot.py`: three states, round trip/malformed/equality, and
  no-song/one-read/dangling-id staging.
- `test_piece_publish.py`: golden shapes/order, omission, dry-run parity, exact
  dedup/restoration, identity differences, preservation, pre-write force refusal.
- `test_lesson_piece_flow.py`: stage A/live B remains A through contract,
  publish, record, and send; re-stage refreshes; legacy never falls back.
- Approved `test_send.py`: snapshot fixture, different live song, and only the
  obsolete live-track assertion replaced. Other existing suites stay unchanged.
- Final Draft PR uses `deep`; install `pip-audit` only in local `.venv`.

## Three least-confident decisions

1. Staging can freeze a pre-correction assignment; re-stage fixes it pre-publish.
2. No created block ids means ownership stays implicit; mismatch refusal trades
   convenience for safety.
3. Legacy unavailable may block required-track sends; truth beats live guessing.
