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
| `baton learner` | Look up learners, sessions, pieces, and past work *(in progress)* |
| `baton lesson` | Stage a lesson, validate a model-written summary, publish it *(in progress)* |
| `baton send` | Send a lesson summary, refusing when required data is missing *(in progress)* |
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
baton doctor
```

`baton doctor` reports every problem at once rather than one per re-run, and
exits `2` while anything is unresolved.

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
preserved exactly.

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
- [ ] **P1** Storage and document adapters (SQLite, Supabase/PostgREST, Notion)
- [ ] **P2** `baton learner`
- [ ] **P3** `baton lesson`, including the JSON summary contract
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
