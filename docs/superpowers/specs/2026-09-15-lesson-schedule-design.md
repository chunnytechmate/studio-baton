# Lesson schedule: weekly slots per learner (design)

Date: 2026-09-15
Status: approved by the studio owner in session, implementation follows this file
Repos: studio-baton (data + CLI), notion-provisioner (service), chunnylab (page)

## What this adds and why

The studio's recurring weekly schedule (which day, what time each learner
comes) lives nowhere today. Days ride as tags on the Notion dashboard row,
times are not recorded at all. This feature gives the schedule a real home in
the learner database, one editor (the studentmanagement page's detail panel),
and a read-only column in that page's table for sorting and filtering.

The future path, agreed with the owner: once the schedule is real data,
surface it on the Notion dashboard (next round), then feed Google Calendar
from it so nobody re-types times into the real calendar.

## Decisions from the owner (2026-09-15)

- Slots start hourly 09:00 through 19:00; one slot is one hour, so the last
  lesson of the day is 19:00-20:00. This menu is studio policy and lives at
  the service layer, not in Baton.
- Two active learners may not hold the same weekday+start. A clash refuses
  the whole save and names the learner that holds the slot.
- One learner may hold several slots on the same day (a two-hour lesson is
  two consecutive slots).
- The schedule becomes the single source of truth for "which days". When the
  page saves a schedule it also rewrites the dashboard row's day tags through
  the existing `/learners/tags` path so the dashboard stays honest, and the
  old day checkboxes leave the edit form.
- Notion dashboard display of times is deliberately out of this round.

## Data model (studio-baton)

New table `lesson_slots`, one row per booked hour:

```sql
CREATE TABLE IF NOT EXISTS lesson_slots (
    id         /* pk, identity */,
    learner_id /* fk learners(id) ON DELETE CASCADE */,
    weekday    text NOT NULL,   -- 'Monday'..'Sunday', the same words the dashboard tags use
    start_time text NOT NULL,   -- 'HH:MM' 24h
    created_at,
    UNIQUE (learner_id, weekday, start_time)
);
CREATE INDEX idx_lesson_slots_learner ON lesson_slots (learner_id, weekday, start_time);
```

- Migrations in `migrations/postgres.sql` and `migrations/sqlite.sql`
  (CREATE TABLE IF NOT EXISTS, so existing databases adopt by running the
  same file). RLS enabled with no policies on the Postgres side, like every
  other table.
- Mapped through config like every table: `db.tables.lesson_slots` and
  `db.fields.lesson_slot` in `defaults.yaml`, resolved in
  `adapters/db/mapping.py` (`Schema.slots`). Required fields: id, learner_id,
  weekday, start_time.
- New domain model `LessonSlot` in `domain/models.py` with `to_dict()`.

Baton itself validates weekday names (the fixed seven), `HH:MM` format, and
duplicates inside one request, but does not enforce the 09:00-19:00 menu: a
public package has no business guessing a studio's teaching hours (same
reason `learner.instruments` starts empty).

## Store contract (studio-baton)

`LearnerStore` gains two methods:

- `list_slots(learner_id | None)` -> all slots for one learner, or every
  slot in the studio, ordered by weekday (Monday first) then start.
- `set_slots(learner_id, slots)` -> replace-all in one transaction, returns
  the final set. Replace-all is the same contract the dashboard tags use:
  the caller sends the whole set, so retries cannot double-book.

Implementations: SqliteStore, PostgrestStore, FakeStore (fakes.py), and the
read-only fallback wrapper passes `list_slots` through. `health()` touches
the new table like the others.

## CLI (studio-baton)

- `baton learner schedule NAME` -> `{learner, slots}` sorted Monday-first
  then time. Empty set is a normal answer.
- `baton learner schedule-set NAME [--slot "Monday 16:00"]... [--dry-run]`
  -> replaces the whole set. No `--slot` clears it.
  - In-set duplicate (weekday, start): UsageError, exit 1.
  - Clashing with another **active** learner's slot: GateError, exit 5, the
    message names the other learner. Slots held by inactive or trashed
    learners never clash: they are not coming.
  - `learner list --json` adds `slots` to each learner entry so the roster
    reads everything in one call.
- Release: 1.7.0 (minor: new subcommand, new optional table; exit codes
  unchanged).

## Service (notion-provisioner)

- `Baton` wrapper: `schedule_learner(name, slots)` drives `learner
  schedule-set`; the GATE payload's message (which names the clashing
  learner) reaches the caller verbatim.
- `GET /learners` passes `slots` through, defaulting to `[]` when the
  installed baton predates them (deploy order below makes this a transitional
  case only).
- New `POST /learners/schedule` with `{name, slots: [{weekday, start}]}`,
  full set, all-or-nothing. Validation before baton is called: name rules as
  everywhere, weekday in the seven, start in the studio menu (09:00..19:00
  on the hour), at most 20 slots. A menu violation or clash answers 400 with
  `error` (+ `remedy` on clash).
- No Notion code changes this round, so the live-round-trip rule for
  `notion.py`/`blueprint.py`/`apply.py` does not trigger. Tests stay on
  fakes.

## Web page (chunnylab, /app/d4f0eaee/studentmanagement)

- `LearnerSummary` gains `slots`; provisioner.ts gains `setLearnerSchedule`.
  api.ts gains action `schedule` with the same validation shape and an audit
  row, like every other action.
- Table: new read-only column "วันที่มาเรียน" between the instrument and
  status columns. One chip per slot: the day's colour dot + short day + time
  (จ 16:00), sorted by day then time; no slots shows "-". Editing happens
  only in the detail panel, per the owner's instruction.
- Sort: every column header is clickable, toggling ascending/descending,
  `aria-sort` kept honest; the day column sorts by earliest slot
  (Monday-first, then time), empty last. An open detail row moves with its
  learner. Pure ordering/filtering logic lives in `src/lib/learner-roster.ts`
  with a test script in `scripts/`, wired into `npm run verify`.
- Filters (owner picked all four): name search, status, instrument, day.
  A counter shows "แสดง X จาก Y คน" and a clear button resets them.
- Detail panel gains "แก้ไขเวลาเรียน": seven day rows (Monday-Sunday with the
  existing dots), each row holding zero or more slots; a slot is a time
  dropdown (09:00..19:00) plus a remove button, each day has an add button.
  Save sends the full set. On success the column chips and the row's
  dataset update in place, then the page composes the dashboard day tags
  (days from the schedule + the instrument tag) and posts them through the
  existing `/learners/tags` path, preserving any tags that are neither day
  names nor instrument tags. When no dashboard row matches (tags null) the
  schedule still saves and the panel says the tags cannot be edited, same
  as the edit form does today.
- The edit form loses its day checkboxes; when the instrument changes it
  still rewrites the instrument tag, keeping the day tags it finds.
- All new Thai copy follows docs/thai-corrections.md (no em dash, no
  คำบัญญัติ; day names are data and keep their existing spelling).

## Deploy order (owner runs all of it by hand)

1. studio-baton 1.7.0 on the home machine (includes running the new
   CREATE TABLE in the Supabase SQL editor), `baton doctor` to confirm.
2. Restart notion-provisioner's service unit with the updated package.
3. Build and `wrangler deploy` chunnylab (after `rm -rf dist/client/play/*`,
   per its AGENTS.md).

Nothing in the chain breaks when step N is done but N+1 is not: old baton
answers the old shape, the service defaults missing slots to empty, and the
page renders an empty column until its own deploy lands.

## Testing

- studio-baton: pytest on real SQLite against the shipped migrations and the
  fake stores (never the live database), ruff, mypy. New tests cover slot
  CRUD, the clash gate, inactive learners not clashing, list carrying slots,
  and JSON shapes.
- notion-provisioner: pytest against the fake baton runner (happy path,
  clash, off-menu time, old-baton tolerance), ruff, mypy.
- chunnylab: `test:learner-roster` for ordering/filter logic, extended
  `test:provisioner` for the new call, then the full `npm run verify` chain,
  and a manual responsive look at 1440x1000 and 390x844.

## Out of scope (next rounds, in order)

1. Show the schedule on the Notion dashboard (a property mirroring slots).
2. Feed Google Calendar from the schedule (no re-typing).
3. Anything that writes to Supabase from outside `baton`.

## Round 2 addendum (2026-09-15, later): standing events on Google Calendar

Approved by the owner: approach A (standing weekly events), auto-sync
after a web schedule save, events marked free (transparent). What the
owner sees on their primary calendar (chunny.fujisawa@gmail.com) is one
weekly recurring series per slot, titled `[icon ]Name · คาบประจำ`, one
hour long, at the slot's weekday and start, forever.

### Shape

- One series per slot: `recurrence: RRULE:FREQ=WEEKLY;BYDAY=<mo..su>`,
  `transparency: transparent`, marked with a private extended property
  `batonStanding=1` plus `learnerId` so a sync can find and replace its
  own events without touching anything a person typed.
- The series starts at the *next* occurrence of the weekday (today never
  counts, the same rule `whenever.py` gives weekday words).
- The booking title shape is `Name (label N)`; standing titles never
  contain ` (`, so the Scheduler's anchored matching cannot meet them.

### Sync semantics

- Full sync (`baton calendar standing-sync`): delete every standing
  series, then create one per slot of every *active* learner. Inactive
  learners' slots produce nothing, which is how someone who stopped
  leaves the calendar. Idempotent; delete-before-create, no local state.
- Scoped sync (`standing-sync --name X`): the same, for one learner's
  events only. Fast enough to chain behind a web save.
- `CalendarStore.list_between` filters standing events out entirely:
  `calendar list`, `book`'s clash gate, and `in-progress` keep answering
  only about booked lessons. The human still sees everything in the
  calendar app, which is where standing times belong.
- A learner deactivated through `/learners/status` is NOT auto-synced in
  this round; the handover says to run a full standing-sync after
  deactivating someone. (Known edge, accepted.)

### Wiring

- `POST /learners/schedule` (service): after baton accepts the schedule,
  the service runs a scoped standing-sync for that learner, best-effort.
  A calendar failure never fails the save; the response carries
  `calendar_synced` (and `calendar_note` on failure) so the page can say
  so.
- Page: the save feedback appends the calendar note when present.

### Testing

- Fakes for everything (fake calendar gains standing support; pipeline
  tests cover active-vs-inactive, scoped vs full, delete-before-create,
  and the list_between filter).
- One live round-trip against the real calendar with a clearly-named
  `zz-standing-test` series: create, list, confirm the filter, delete,
  confirm gone. Same session, like the Notion zz- learners.

### Release

1.8.0. Deploy order unchanged: PyPI release + venv reinstall here, then
service restart, then the page deploy.
