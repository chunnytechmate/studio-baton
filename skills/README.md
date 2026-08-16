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
   the prose; with `--json` every command prints one document on stdout.
2. **Exit 3 means stop and ask.** The payload carries `candidates`. Show them
   and wait — never pick one.
3. **Exit 5 means the data is incomplete.** There is no override flag. Report
   what is missing and fix that.
4. **Exit 4 means your JSON was wrong.** The payload says where. Correct it and
   resubmit.
5. **Never work around a failure by calling an API directly.** If a command
   cannot do it, say so and stop.

| Code | Meaning | What the agent does |
| --- | --- | --- |
| `0` | Done | Report the result |
| `2` | Configuration is broken | Run `baton doctor`, report, stop |
| `3` | Ambiguous | Show `details.candidates`, ask, re-run with an exact name |
| `4` | Submitted content invalid | Fix the fields in `details.violations`, resubmit |
| `5` | Gate blocked it | Report `details.missing`. **Do not retry** |
| `6` | Upstream failed | Report. Re-running later is safe |
| `7` | State needs an audit | Report. Do not force anything |
| `8` | Job still running | Wait, or report the job id |
