# Adopting a database you already have

Do not run the migration. Point Baton at your own names instead: that is what
`db.tables` and `db.fields` are for, and it is the difference between adopting
Baton and migrating to it.

## Map it

```yaml
db:
  driver: supabase
  tables:
    learners: students          # your table names
    sessions: student_pages
    pieces: songs
    works: student_works
  fields:
    learner:
      id: id
      name: name
      instrument: instrument
      tone: tone
      has_instrument: has_instrument
      current_piece_id: current_song_id
      is_active: is_active
    session:
      id: id
      learner_id: student_id
      number: week              # your column for the session number
      doc_id: page_id
```

Then:

```bash
baton doctor
```

It reads one row from every configured table and names any column that does not
exist, rather than waiting for a pipeline to fail at 2am.

## What Baton requires

Only this much:

| Entity | Required | Optional |
| --- | --- | --- |
| learner | `id`, `name` | `instrument`, `tone`, `has_instrument`, `current_piece_id`, `is_active`, `deleted_at` |
| session | `id`, `learner_id`, `number` | `doc_id` |
| piece | `id`, `title` | `source_link`, `practice_track`, `sheet_link` |
| work | `id`, `learner_id`, `title` | `type`, `video_link`, `performed_date` |

Columns Baton does not model are read and kept on each record's `raw`, so
nothing is lost by passing data through it.

Session **status** deliberately is not a column. It lives on the session
document, and copying it into the database is how the two came to disagree in
the system Baton replaces.

Mapping `is_active` turns on `learner activate`/`deactivate` and the
active-only default of `learner list` and the rosters. Left unmapped, every
learner reads as active and nothing about matching changes.

Mapping `deleted_at` turns on `learner trash`/`untrash` and hides trashed
learners even from `learner list --all`, a distinct, further status from
`is_active`. Left unmapped, every learner reads as not trashed.

## Print the reference schema

If you want to compare against what Baton would have created:

```bash
baton schema postgres
baton schema sqlite
```
