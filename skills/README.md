# Skills

Thin wrappers that let an agent drive Baton. Each one is a decision table, not
a manual: a trigger, the exact command, and what to do about each exit code.

They contain **no API calls, no JSON payloads, and no `python3 -c`**. Anything a
model would have had to assemble by hand is a subcommand instead — that is the
whole point of the CLI underneath, and a skill that reintroduced raw API calls
would undo it. `tests/test_skills.py` enforces this: it fails if a skill names a
command the CLI does not have, or smuggles a raw API call back in.

## Installing

Copy or symlink the directories into wherever your harness reads skills from:

```bash
# Claude Code
ln -s "$PWD/skills/"* ~/.claude/skills/

# OpenClaw
ln -s "$PWD/skills/"* ~/.openclaw/workspace/skills/
```

Baton must be installed and a profile configured first:

```bash
pip install studio-baton
export BATON_PROFILE=~/my-studio
baton doctor
```

`BATON_PROFILE` has to be visible to the agent's shell. In a container, set it
in the container's environment rather than a login file — an agent's shell is
often not a login shell.

## The rules every skill follows

1. **Run the command. Read the exit code. Branch on the number.** Never parse
   the prose; with `--json` every command prints one document on stdout — on a
   crash and on a kill too, so an empty stdout means the process never ran.
2. **Exit 3 means stop and ask.** The payload carries `candidates`. Show them
   and wait — never pick one.
3. **Exit 5 means the data is incomplete.** There is no override flag. Report
   what is missing and fix that.
4. **Exit 4 means your JSON was wrong.** The payload says where. Correct it and
   resubmit.
5. **Never work around a failure by calling an API directly.** If a command
   cannot do it, say so and stop.
6. **Exit 9 is a bug in Baton, not in your command line.** Report the message
   and stop; re-running with different arguments cannot help.
7. **"Already sent" is not a failure to fix.** A send refused as a duplicate
   (exit `5`, message says *already sent*) means the message went out. Say so.
   `--again` exists for a person who has confirmed it never arrived — never
   reach for it on your own.
8. **Check the version before trusting this table.** `baton --version --json`
   prints the version and every command that exists. If a command a skill names
   is missing there, the installed Baton is older than the skill; say so rather
   than guessing at a substitute.

| Code | Meaning | What the agent does |
| --- | --- | --- |
| `0` | Done | Report the result |
| `2` | Configuration is broken | Run `baton doctor`, report, stop |
| `3` | Ambiguous | Show `details.candidates`, ask, re-run with an exact name |
| `4` | Submitted content invalid | Fix the fields in `details.violations`, resubmit |
| `5` | Gate blocked it | Report `details.missing`. **Do not retry** |
| `6` | Upstream failed | Report. Re-running later is safe |
| `7` | State needs an audit | Report. Do not force anything |
| `8` | Job still running, or another run is in the way | Wait, or report the job id. Two agents on one profile collide here — the other run is not yours to stop |
| `9` | Baton itself crashed | Report `message` and `details.traceback`. **Do not retry** |
| `130` / `143` | Interrupted / killed (a harness time limit) | The work may or may not have completed. Check before re-running |
