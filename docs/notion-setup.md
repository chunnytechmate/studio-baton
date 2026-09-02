# Setting up Notion

Baton needs an integration token and a database it is allowed to see. The
second half is where almost everyone gets stuck.

## 1. Create an integration

Go to [notion.so/my-integrations](https://www.notion.so/my-integrations), create
an internal integration, and copy its secret. That is `NOTION_API_TOKEN`.

## 2. Share the pages with it

**This is the step people miss.** An integration can only see pages that have
been explicitly shared with it. Until you do this, every request returns 404 (
not 403), so it looks like the page does not exist.

On your session database (and on the parent page quick notes live under):
open the `⋯` menu → *Connections* → add your integration. Sharing a parent page
shares everything under it.

If `baton doctor` reports a 404, this is why.

## 3. Point the config at your property names

Baton does not require particular names. Tell it yours:

```yaml
docs:
  properties:
    status: Status          # whatever your column is called
    titles: Repertoire
    date: Lesson date
  statuses:
    done: Complete          # whatever your values are
    in_progress: In progress
    not_started: Not started
```

A property may be a `status`, a `select`, or `rich_text`: Baton reads whichever
you built. Run `baton doctor` to confirm the mapping before relying on it.

## 4. Protect what a rewrite must not touch

When a summary is republished, blocks matching `docs.preserve` survive and
everything else is replaced. Recordings and attachments are protected by
default; add rules for anything else you keep on a session page:

```yaml
docs:
  preserve:
    - {type: video}
    - {type: embed}
    - {type: callout, icon: "🎧"}       # practice-track callouts
    - {type: heading_3, startswith: "🎵"}
```

Check it with `baton lesson publish "<name>" --dry-run`, which reports what
would be kept and what would be removed without changing anything.
