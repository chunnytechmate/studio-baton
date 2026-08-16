# Running a private deployment

The intended shape is one public codebase and a private repository holding only
what is yours. Forking drifts within a month; a config-only overlay does not.

```
my-studio/                 # private
├── baton.yaml             # your tables, properties, contacts, labels
├── .env                   # secrets — never committed in the clear
├── theory.json            # your teaching notes, referenced by callout id
├── state/                 # drafts, published records, job state
├── data/                  # SQLite, if that is your driver
└── requirements.txt       # how you pin Baton itself — see below
```

Baton has not been released to PyPI yet (`0.1.0.dev0`), so pin it by git ref
until it has been:

```
studio-baton @ git+https://github.com/chunnytechmate/studio-baton@main
```

Pin a tag rather than `main` as soon as there is one: a private overlay that
tracks a moving branch will one day pick up a change to the config schema
between two runs of the same nightly job.

## Install it

```bash
git clone git@github.com:you/my-studio.git ~/my-studio
pip install -r ~/my-studio/requirements.txt
export BATON_PROFILE=~/my-studio
baton doctor
```

## Secrets

`baton.yaml` names environment variables and never contains a value, so the
config file is safe to commit to a private repository. `.env` is not: encrypt it
([sops](https://github.com/getsops/sops) with age works well) or keep it out of
git entirely and inject the variables from your container's secret store.

## Keeping personal data out of the public repo

`tools/check_leaks.py` blocks harness-specific paths, credential-shaped
literals, and editor backups. It cannot know your learners' names, so the list
lives with you:

```bash
# in the private overlay
cat > .denylist <<'NAMES'
# One term per line. Never commit this to a public repository.
NAMES

BATON_DENYLIST=~/my-studio/.denylist python tools/check_leaks.py
```

Without `BATON_DENYLIST` the check says it was skipped rather than reporting a
pass — it never claims to have verified something it did not.

## Upgrading

Bump the pin, run the checks, then run something harmless:

```bash
pip install -r requirements.txt
baton doctor
baton learner list
```

Configuration is versioned (`version: 1`). A release that changes the shape will
say so and refuse to load an older one rather than misreading it.
