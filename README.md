# Studio Baton

Scripted operations for a one-to-one teaching studio: learner records, session
documents, lesson summaries, messaging, video, and calendar — driven by one
command-line tool with a stable exit code contract.

Baton exists because these workflows were being run by an AI agent reading prose
instructions, assembling API calls by hand. That works when the model is strong
and fails quietly when it is not. Everything that can be a script here is a
script, so the model is left with the one job only a model can do — writing the
summary — and even that is submitted as JSON validated against a schema.

> **Status: early.** The foundation (configuration, state, CLI contract) is in
> place; the pipelines are being ported. See [Roadmap](#roadmap).

## What it does

| Command | Job |
| --- | --- |
| `baton doctor` | Check config, credentials, and drivers before anything runs |
| `baton config` | Show the configuration the tool actually resolved |
| `baton job` | Run long work detached, then check on it, wait, or stop it |
| `baton learner` | Look up learners, sessions, pieces, and past work |
| `baton lesson` | Stage a lesson, validate a model-written summary, publish it |
| `baton send` | Send a lesson summary, refusing when required data is missing |
| `baton video` | Drive → encode → upload → link back, resumable *(in progress)* |
| `baton calendar` | Book lessons, keeping documents and calendar in step *(in progress)* |
| `baton notes` | Push a note or a Markdown file to a documents page *(in progress)* |

## Install

```bash
pip install studio-baton          # core
pip install "studio-baton[google]" # plus Drive, YouTube, Calendar
```

## Quickstart

```bash
cp -r profiles/example ~/my-studio
cd ~/my-studio
cp .env.example .env && $EDITOR .env      # credentials
$EDITOR baton.yaml                        # your schema, labels, contacts
export BATON_PROFILE=~/my-studio

sqlite3 data/studio.db < migrations/sqlite.sql        # schema
sqlite3 data/studio.db < migrations/seed_example.sql  # optional sample data

baton doctor
```

`baton doctor` reports every problem at once rather than one per re-run, and
exits `2` while anything is unresolved. It checks the schema mapping too — a
column named in `baton.yaml` that does not exist is caught here rather than at
2am inside a pipeline. Add `--offline` to skip the checks that need a network.

Already have a database? Do **not** run the migration. Point `db.tables` and
`db.fields` at your own names and let `baton doctor` confirm the mapping.

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

**Reads fall over; writes never do.** With `db.fallback` set, a read served
during an outage comes from the secondary store. A write does not: a write that
lands only in a replica is a permanent divergence that nothing reconciles, so
it fails loudly instead.

**"Latest" means the newest session that happened, never the highest number.**
Sessions get skipped — illness, cancellations, pages created in advance — so
session 12 existing says nothing about whether session 12 took place. And the
next free session must be both unstarted *and* empty: a page marked "not
started" that already has blocks on it is someone's work in progress, and
handing it back as free is how a summary overwrites a draft.

```bash
baton learner latest "Ada Whitfield"   # newest done, by document date
baton learner next   "Ada Whitfield"   # lowest unstarted *and* empty
baton learner in-progress              # everyone mid-session, this morning
```

A status the profile does not describe — a studio adds "Cancelled" — maps to
*unknown* rather than being filed as one of the three. Unknown is never offered
as the next free session.

**Long jobs resume.** Video processing and publishing record each completed step
atomically, so a crash mid-run is re-runnable without re-uploading a video or
duplicating a page block.

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
over that. To see the result:

```bash
baton config show                 # whole tree
baton config show docs.properties # one branch
```

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
- [ ] **P4** `baton send` with the fail-closed gate; LINE and Telegram
- [ ] **P5** `baton video`, with `--detach` wired to `baton job`
- [ ] **P6** `baton calendar`
- [ ] **P7** `baton notes`
- [ ] **P8** Agent skill definitions (`skills/`)
- [ ] **P9** `baton init`, migrations, docs

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
```

## License

MIT. See [LICENSE](LICENSE).
