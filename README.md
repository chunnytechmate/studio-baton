# Studio Baton

Scripted operations for a one-to-one teaching studio: learner records, session
documents, lesson summaries, messaging, video, and calendar — driven by one
command-line tool with a stable exit code contract.

Baton exists because these workflows were being run by an AI agent reading prose
instructions, assembling API calls by hand. That works when the model is strong
and fails quietly when it is not. Everything that can be a script here is a
script, so the model is left with the one job only a model can do — writing the
summary — and even that is submitted as JSON validated against a schema.

> **Status: usable, not yet 1.0.** Every pipeline is ported and tested; the
> configuration format is versioned and will not change shape without saying
> so. See [Roadmap](#roadmap).

## What it does

| Command | Job |
| --- | --- |
| `baton init` | Create a profile that already runs |
| `baton doctor` | Check config, credentials, and drivers before anything runs |
| `baton config` | Show the configuration the tool actually resolved |
| `baton job` | Run long work detached, then check on it, wait, or stop it |
| `baton learner` | Look up learners, sessions, pieces, and past work |
| `baton lesson` | Stage a lesson, validate a model-written summary, publish it |
| `baton send` | Send a lesson summary, refusing when required data is missing |
| `baton video` | Collect recordings → encode → publish → link back, resumable |
| `baton calendar` | Book lessons, keeping documents and calendar in step |
| `baton notes` | Push a note or a Markdown file to a documents page |

## Install

```bash
pip install studio-baton          # core
pip install "studio-baton[google]" # plus Drive, YouTube, Calendar
```

## Quickstart

```bash
baton init ~/my-studio --sample-data
export BATON_PROFILE=~/my-studio
cd ~/my-studio && cp .env.example .env && $EDITOR .env
baton doctor
baton learner list
```

`init` asks a handful of questions — your language, timezone, where records
live, how messages are sent, what you call a student and a session — and writes
a config, an `.env.example` listing exactly the variables *that* profile needs,
and a database with the schema already in it. Pass every answer as a flag with
`--yes` to run it unattended.

Baton reads the profile's `.env` when it loads the profile, so filling that
file in is enough — nothing needs exporting. A variable already set in the
environment wins over the file, which is how a container or a secret store
injects credentials without the file being present at all.

`baton doctor` reports every problem at once rather than one per re-run, and
exits `2` while anything is unresolved. It checks the schema mapping too — a
column named in `baton.yaml` that does not exist is caught here rather than at
2am inside a pipeline. Add `--offline` to skip the checks that need a network.

Already have a database? Do **not** run the migration. Point `db.tables` and
`db.fields` at your own names and let `baton doctor` confirm the mapping — see
[Adopting a database you already have](docs/adopting-an-existing-schema.md).
`baton schema postgres` prints the reference SQL if you want to compare.

## Design

**One profile directory holds everything installation-specific.** A profile is a
`baton.yaml` plus whatever private material you keep beside it. The code never
reaches outside it, which is what lets a private deployment be config-only
while the code stays a shared dependency.

**Configuration names credentials; it never contains them.** Settings ending in
`_env` name an environment variable. A profile is safe to keep in a private
repository and safe to paste into a bug report.

**Nothing about the domain is hardcoded.** Table names, column names, document
property names, status values, recipient aliases, and the words for
"student"/"week"/"piece" all live in `baton.yaml`. Adopting an existing Notion
database or Postgres schema is a config change, not a migration.

**Exit codes are the interface.**

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Bad invocation |
| `2` | Configuration or environment is incomplete — nothing was attempted |
| `3` | Ambiguous input; a person must choose. A `candidates` list is returned |
| `4` | Submitted content failed schema validation; nothing was written |
| `5` | A fail-closed gate blocked the action. **No override flag exists** |
| `6` | Upstream service failed after retries |
| `7` | Local job state is inconsistent and needs an audit |
| `8` | A background job is still running, or is in the way |

With `--json`, every command — success or failure — prints one JSON document on
stdout and nothing else. Progress goes to stderr. An agent reads the code and
the document; it never parses prose.

**Gates fail closed.** A lesson message is not sent when a required field is
missing. There is deliberately no way to force it: the fix is to supply the
data. This is the behaviour that made the original system trustworthy and it is
preserved exactly:

```
$ baton send lesson "Ada Whitfield" --to me
✗ Refusing to send the lesson message for Ada Whitfield: missing doc_link.

  missing:
    - doc_link: `doc_link` is empty

  Nothing was sent. Supply the missing items, then re-run. There is no flag to bypass this check.
exit=5
```

What counts as required is configuration (`gates.send_lesson_required`), so a
studio sets its own standard of completeness; the block itself is not
negotiable. What is sent is what was *published* — the message comes from the
record stored at publish time, and the links are Baton's own, which is why
links are forbidden inside the summary a model writes.

Several learners go through one invocation. A refusal for one does not abandon
the rest, and the exit code plus the report say exactly which did not go:

```bash
baton send batch --to me --learner "Ada" --learner "Bruno" --learner "Clara"
```

The same stance applies to names. A typed name resolves only on an exact match
or a configured alias — a partial match never resolves, *even when it is the
only one*, because the second person with that name is exactly the case that
would go wrong silently. Ambiguity exits `3` and returns the candidates:

```json
{"error": "needs_human",
 "message": "“Nam” is not an exact match for any student.",
 "details": {"candidates": [{"id": "4", "name": "Namo (guitar)"},
                            {"id": "5", "name": "Namo (drums)"}]}}
```

**The model returns data, never prose.** A summary is the one thing Baton
cannot script, so it is the one thing a model writes — as JSON against a schema,
which Baton then renders itself. The loop is three commands:

```bash
baton lesson contract "Ada Whitfield"              # schema + context, in one document
baton lesson ingest   "Ada Whitfield" --file s.json  # validated, or exit 4
baton lesson render   "Ada Whitfield"              # deterministic preview
baton lesson publish  "Ada Whitfield"
```

Rules a JSON Schema cannot express are checked in code, because they are exactly
the ones a small model ignores when they are written as prose — no emoji in the
parent's message, no links, one line per field, and every theory callout
referenced by an id that exists. A rejection is total: nothing is stored, and
every violation comes back at once with a pointer to it.

```
$ baton lesson ingest "Ada Whitfield" --file summary.json
exit 4 | contract | The lesson summary does not match the required structure (3 problems).
  /short_summary/covered     contains emoji, which this profile does not allow in messages
  /short_summary/progress    contains a link
  /callouts/0                `tremolo-picking` is not in this studio's theory notes
```

Callout text comes from the studio's own `theory.json`; the model supplies only
the id. It cannot write theory content into a document at all.

The same reasoning covers notes. The skill this replaces handed a model the
API shape and a `curl` invocation and asked it to build the block JSON, split
it at the store's 100-child ceiling, and retry — all mechanical, and all
invisible when done wrong, because a note that lost a line just looks shorter
than you remembered. It is a parser now:

```bash
baton notes preview --file today.md   # what it becomes, touching nothing
baton notes push --file today.md
```

The conversion is total: every line produces exactly one block, and anything
unrecognised becomes a paragraph rather than being dropped.

**A rewrite cannot destroy what it did not write.** Updating a summary replaces
only the blocks the `docs.preserve` policy does not protect, so uploaded
recordings, sheet-music embeds and practice-track callouts survive. Blocks are
appended *before* the old ones are deleted: a failure halfway then leaves a
duplicated section, which is recoverable, rather than an empty page with the
recordings gone. The policy is an allowlist expressed as data:

```yaml
docs:
  preserve:
    - {type: video}
    - {type: embed}
    - {type: callout, icon: "🎧"}
```

**Booking happens in an order that cannot leave two records disagreeing.**
A lesson is marked in progress on its document *first*; the calendar event is
created only if that succeeded. Creating the event first and then failing on
the document leaves a lesson the sessions know nothing about — the teacher
trusts the calendar, the pipeline trusts the documents, and they drift apart
until someone reconciles them by hand. Cancelling runs the chain backwards for
the same reason, and refuses to reach further back than
`calendar.rollback_window_days`, because rewriting last week's records is
usually a mistake rather than an intention.

**Date arithmetic is code, not a model's job.** An off-by-one books a lesson
on the wrong day and nobody finds out until a family arrives to an empty room:

```bash
$ baton calendar date พน      # shorthand tokens are configuration
2026-08-17
$ baton calendar date "next tuesday"
✗ `next tuesday` is not a date Baton understands.
  Use YYYY-MM-DD, a signed offset like +2, or one of: today, tomorrow, yesterday, พน, มร, วน, สน.
```

A whole day is booked from the list a teacher actually writes. A slot ends
when the next begins, and a free period is skipped but still bounds the slot
before it — without that, the lesson before an hour off silently doubles:

```bash
baton calendar schedule tomorrow --text "17:00 Ada Whitfield
18:00 -
19:00 Bruno Castell"
```

**Reads fall over; writes never do.** With `db.fallback` set, a read served
during an outage comes from the secondary store. A write does not: a write that
lands only in a replica is a permanent divergence that nothing reconciles, so
it fails loudly instead.

**"Latest" means the newest session that happened, never the highest number.**
Sessions get skipped — illness, cancellations, pages created in advance — so
session 12 existing says nothing about whether session 12 took place. And the
next free session is where a new lesson may land: a page in progress is the
target while it is fresh — the studio's flow books a lesson, the page turns
In progress, and the summary is written onto that page — and only a page still
in progress more than `learner.next_stale_days` past its date is passed over
as abandoned, so one missed week cannot hold every later week hostage. A page
marked "not started" that already has blocks on it is someone's work in
progress, and handing it back as free is how a summary overwrites a draft.

```bash
baton learner latest "Ada Whitfield"   # newest done, by document date
baton learner next   "Ada Whitfield"   # where the next lesson lands
baton learner in-progress              # who still owes a summary (calendar window)
```

A status the profile does not describe — a studio adds "Cancelled" — maps to
*unknown* rather than being filed as one of the three. Unknown is never offered
as the next free session.

**Long jobs resume.** Video processing and publishing record each completed step
atomically, so a crash mid-run is re-runnable without re-uploading a video or
duplicating a page block.

Three properties hold the video pipeline together, each because its absence
loses or duplicates a recording:

- **Nothing is deleted until everything else succeeded.** Source clips are
  trashed last, after the upload and the link. Until then they are the only
  copy, and a crash before the upload would lose the lesson permanently.
- **A completed upload is never repeated.** The video id is recorded the
  moment the platform returns it, so a resume cannot publish a second copy of
  a child's lesson with no way to tell which link was sent.
- **One learner's failure does not stop the others.** A corrupt clip from one
  phone must not mean nobody's recording goes out that night.

```bash
baton video run --dry-run     # what is waiting
baton video run --detach      # background, survives the session
baton video status            # per-learner progress through the steps
baton video resume            # continue whatever did not finish
```

```
  ✗ Ada Whitfield        ##.....  failed
      ffmpeg failed: Invalid data found when processing input
      next step: combined

  steps: downloaded → combined → session_resolved → uploaded → doc_linked → cleaned → source_trashed
```

Clips arrive from Google Drive or a watched local directory
(`media.source.driver`), so the pipeline can be tried without a Google
account. A source folder resolves to a learner by exact name only — the same
stance as everywhere else, because uploading one child's lesson onto another
child's page is not worth the convenience.

**Long jobs also detach.** Encoding and uploading run for tens of minutes —
longer than an agent session, an SSH connection, or anyone's patience. Any
command can be handed to a supervisor that outlives the shell that started it:

```bash
baton job spawn --name nightly -- baton video run   # returns at once
baton job list                                      # what is running
baton job wait <id> --timeout 600                   # exits 8 if still going
baton job logs <id> --tail 50
baton job stop <id>                                 # SIGTERM, then SIGKILL
```

`job wait` exits with the job's *own* exit code, so waiting on a detached run
and running it in the foreground are indistinguishable to a caller.

Two properties make this safe to point an agent at. A job whose supervisor died
without recording an outcome reads as `orphaned` (exit `7`) rather than silently
"running forever" — liveness is checked, not assumed. And pipelines take a
whole-run lock held by an open file handle, so a second run cannot collide with
a first; the OS drops the lock however the holder dies, which means there is no
stale lockfile to clear by hand:

```
✗ Another run already holds video.lock.
  Wait for it to finish (`baton job list`), or stop it (`baton job stop <id>`), then re-run.
```

## Configuration

`src/baton/defaults.yaml` is the documented, complete default. A profile is
deep-merged over it, then `BATON__SECTION__KEY` environment variables are merged
over that. The profile's `.env` is loaded into the environment first, so it can
carry a `BATON__…` override as well as a credential — and an exported variable
still beats the file either way. To see the result:

```bash
baton config show                 # whole tree
baton config show docs.properties # one branch
```

## Driving it from an agent

`skills/` holds a wrapper per pipeline for harnesses that load skill files
(Claude Code, OpenClaw, and anything with the same convention):

```bash
ln -s "$PWD/skills/"* ~/.claude/skills/
```

Each is a decision table — a trigger, the exact command, and what to do about
each exit code — not a manual. None of them contains an API call, a JSON
payload, or a `python3 -c`: everything a model would otherwise assemble by hand
is a subcommand instead, which is what the CLI underneath is for.

`tests/test_skills.py` keeps them honest. It fails if a raw API call reappears,
if a skill stops documenting its exit codes, or if one grows past 120 lines —
the original ran to 400 lines of prose, which is how its rules stopped being
followed. It also checks that the commands a skill names exist and parse, but
only for lines that *begin* with `baton ` — a command written inside a markdown
table is not checked today, which is most of `student-lookup`.

## Roadmap

The port from the original skills runs in phases; each lands behind tests and is
diffed against the legacy scripts before the old path is retired.

- [x] **P0** Package skeleton, configuration, state layer, exit contract, CI
- [x] **P0.5** Detached jobs (`baton job`), run locking, orphan detection
- [x] **P1** Storage and document adapters (SQLite, Supabase/PostgREST, Notion),
      the name-resolution gate, migrations, and in-memory fakes
- [x] **P2** `baton learner` — lookups joined across both stores
- [x] **P3** `baton lesson` — the JSON summary contract and safe publishing
- [x] **P4** `baton send` — the fail-closed gate; LINE, Telegram, and webhook drivers
- [x] **P5** `baton video`, with `--detach` wired to `baton job`
- [x] **P6** `baton calendar`
- [x] **P7** `baton notes`
- [x] **P8** Agent skill definitions (`skills/`)
- [x] **P9** `baton init`, migrations, docs
- [x] **P10** The parity harness (`tools/parity.py`)

Every phase has landed, and the read paths have now been exercised against a
real studio: `doctor` passes against live Supabase, Notion, and LINE, one real
LINE message has been delivered, and the parity harness below agrees with the
scripts being replaced on every learner it compares. What has *not* run against
anything real is the write-heavy end — video encoding and upload, and booking
or cancelling a real calendar event. Treat the parity run — and a first send to
yourself, never to a family — as the gate before any of this is trusted with a
real lesson.

## Replacing something that already works

A rewrite is trustworthy when it gives the same answers as the thing it
replaces, on that studio's own data — not when its own tests pass. Tests were
written from the same understanding as the code, so they share its blind spots.
The old script does not.

```bash
tools/parity.py --spec parity.yaml
```

It runs both sides of each case and diffs the fields that matter. Read-only by
design: lookups, never a send, a publish, or an upload. When one side cannot
run it is reported as a difference, never as agreement — a harness that scores
silence as a pass would give the go-ahead to retire a working system. Run it daily until the
answers have agreed for long enough to trust, retiring the read-only paths
first and the ones that message families last — a wrong lookup is noticed, a
wrong message to a parent is not recoverable.

`parity.yaml` is not in this repository, and no example of it could be: it
names the paths of *your* legacy scripts and the fields of *your* schema. Write
your own — the format, with a worked case, is documented at the top of
`tools/parity.py`.

Three things about running the old system will look like disagreements when
they are really setup problems. All three cost a run to find:

- **Imports resolve against the legacy workspace, not yours.** Set `PYTHONPATH`
  to the directory the old scripts assume they live under, or every case fails
  identically on an import.
- **A read gate can expire mid-run.** If the legacy side is behind a gate with
  a window (fifteen minutes, in the system this replaces), a run longer than
  the window turns every remaining learner into a difference. Widen the window
  to longer than the run takes, or re-seed it before starting. The harness is
  right to report those as differences rather than agreement — that property is
  pinned by its own tests — so the fix belongs in the setup, not the harness.
- **The old side may need dependencies Baton does not have.** `httpx` and
  `supabase` are not Baton's, and the legacy scripts will not start without
  them.

## Documentation

- [Setting up Notion](docs/notion-setup.md) — including the sharing step that
  makes every request 404 until you do it
- [Adopting a database you already have](docs/adopting-an-existing-schema.md)
- [Running a private deployment](docs/private-overlay.md) — one public
  codebase, a config-only private overlay

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
```

## License

MIT. See [LICENSE](LICENSE).
