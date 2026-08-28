"""Reading a Notion page id and a week number out of a URL a person pasted.

``learner add`` takes the links straight from whatever a browser produced —
``notion.site``, ``notion.so``, and the ``app.notion.com/p/…`` share form all
carry the same 32 hex characters, dashed or not. Getting this wrong writes a
session pointed at the wrong page, so an unrecognised URL is refused rather
than guessed at.
"""

from __future__ import annotations

import re

#: 32 hex characters, dashed or not, ending at a query string, a path
#: separator, or the end of the string — so a page id embedded mid-URL is
#: found without also matching a longer hex run that happens to contain one.
_PAGE_ID = re.compile(
    r"([0-9a-f]{8})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{12})(?=[?/]|$)"
)
_DASHED_PAGE_ID = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=[?/]|$)"
)
#: The slug Notion titles a page's URL with: a leading number, then the id.
#: That number is usually the week, because the page title carries it.
_WEEK_SLUG = re.compile(r"/(\d+)-[0-9a-f]{8,}")


def parse_page_id(url: str) -> str | None:
    """The page id in a Notion URL, dashed, or ``None`` if none is found.

    Accepts ``notion.site``, ``notion.so``, and ``app.notion.com/p/…`` links,
    with or without a query string, and either hex form the id may already
    be in.
    """
    folded = url.strip().casefold()
    dashed = _DASHED_PAGE_ID.search(folded)
    if dashed:
        return dashed.group(1)
    bare = _PAGE_ID.search(folded)
    if bare:
        return "-".join(bare.groups())
    return None


def detect_week(url: str) -> int | None:
    """The week a URL's slug names, or ``None`` when it names none.

    Reads the number Notion's own URL puts in front of a page's id — which is
    the page title, so it tracks whatever a person named the page rather than
    an assumption about ordering.
    """
    match = _WEEK_SLUG.search(url.strip().casefold())
    return int(match.group(1)) if match else None
