"""Turning what a person types into a date and a time.

The original told the agent to "parse the shorthand into YYYY-MM-DD before
calling the script". That is arithmetic, and arithmetic is the last thing to
leave to a language model — an off-by-one here books a lesson on the wrong day
and nobody notices until a family arrives to an empty room.

So it is a function, and a command. Shorthand tokens are configuration, because
"พน" means tomorrow to one studio and nothing at all to another.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..errors import UsageError

#: Tokens every profile understands, regardless of language.
BUILTIN_OFFSETS = {
    "today": 0,
    "tomorrow": 1,
    "yesterday": -1,
}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OFFSET = re.compile(r"^([+-])(\d{1,3})$")
_TIME = re.compile(r"^(\d{1,2})[:.](\d{2})$")
_HOUR_ONLY = re.compile(r"^(\d{1,2})$")


def today_in(timezone: str) -> date:
    """The current date in the studio's timezone.

    Explicitly not the server's date: a studio in Bangkok running on a UTC
    host would otherwise book "today" onto yesterday for seven hours a day.
    """
    return datetime.now(ZoneInfo(timezone)).date()


def parse_date(
    value: str,
    *,
    timezone: str = "UTC",
    shorthand: Mapping[str, object] | None = None,
    reference: date | None = None,
) -> date:
    """Resolve a date expression.

    Accepts an ISO date, a builtin token, a configured shorthand token, or a
    signed day offset like ``+3``.

    Args:
        value: What the person typed.
        timezone: Studio timezone, used to decide what "today" means.
        shorthand: Token to day-offset map from ``calendar.date_shorthand``.
        reference: Treat this as today. For tests.

    Returns:
        The resolved date.

    Raises:
        UsageError: The expression is not one Baton understands. The message
            lists what it does understand rather than guessing.
    """
    raw = value.strip()
    if not raw:
        raise UsageError("No date was given.", remedy="Pass a date, or `today`.")

    if _ISO_DATE.match(raw):
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise UsageError(
                f"`{raw}` looks like a date but is not a real one.",
                remedy="Use YYYY-MM-DD.",
            ) from exc

    base = reference or today_in(timezone)
    folded = raw.casefold()

    if folded in BUILTIN_OFFSETS:
        return base + timedelta(days=BUILTIN_OFFSETS[folded])

    for token, offset in (shorthand or {}).items():
        if str(token).strip().casefold() == folded:
            try:
                return base + timedelta(days=int(str(offset)))
            except (TypeError, ValueError) as exc:
                raise UsageError(
                    f"The shorthand `{token}` is configured with a non-numeric offset.",
                    remedy="calendar.date_shorthand maps a token to a number of days.",
                ) from exc

    match = _OFFSET.match(raw)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        return base + timedelta(days=sign * int(match.group(2)))

    known = sorted({*BUILTIN_OFFSETS, *(str(k) for k in (shorthand or {}))})
    raise UsageError(
        f"`{raw}` is not a date Baton understands.",
        remedy=f"Use YYYY-MM-DD, a signed offset like +2, or one of: {', '.join(known)}.",
        details={"understood": known},
    )


def parse_time(value: str) -> time:
    """Resolve a time of day written as ``17:00``, ``17.00``, or ``17``.

    Raises:
        UsageError: Not a time, or not a real one.
    """
    raw = value.strip()
    match = _TIME.match(raw) or _HOUR_ONLY.match(raw)
    if not match:
        raise UsageError(
            f"`{raw}` is not a time.",
            remedy="Write it as 17:00 or 17.00.",
        )
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.lastindex and match.lastindex > 1 else 0
    if hour > 23 or minute > 59:
        raise UsageError(
            f"`{raw}` is not a real time.",
            remedy="Hours are 0-23 and minutes 0-59.",
        )
    return time(hour=hour, minute=minute)


def combine(day: date, moment: time, timezone: str) -> datetime:
    """A timezone-aware datetime in the studio's own timezone."""
    return datetime.combine(day, moment, tzinfo=ZoneInfo(timezone))


def parse_schedule(
    text: str,
    *,
    default_minutes: int = 60,
    free_markers: tuple[str, ...] = ("-", "—", "free", "ว่าง"),
) -> list[tuple[time, time, str]]:
    """Parse a day's teaching list into ``(start, end, name)`` rows.

    The format a teacher actually writes: one line per slot, time first, then
    who. A slot's end is the next slot's start, which is how a back-to-back
    day really works; the last slot gets ``default_minutes``.

    Free periods are skipped rather than booked — but they still bound the
    previous slot, so an hour off does not silently extend the lesson before it.

    Raises:
        UsageError: A line is not ``<time> <name>``, naming the line.
    """
    rows: list[tuple[time, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        try:
            start = parse_time(parts[0])
        except UsageError as exc:
            raise UsageError(
                f"Line {number} does not start with a time: {stripped!r}",
                remedy="Each line is `17:00 Name`, or `17:00 -` for a free slot.",
            ) from exc
        name = parts[1].strip() if len(parts) > 1 else ""
        rows.append((start, name))

    rows.sort(key=lambda row: row[0])

    booked: list[tuple[time, time, str]] = []
    for index, (start, name) in enumerate(rows):
        if not name or name.casefold() in {marker.casefold() for marker in free_markers}:
            continue
        if index + 1 < len(rows):
            end = rows[index + 1][0]
        else:
            # datetime arithmetic on an arbitrary anchor date: the date itself
            # is irrelevant, only the midnight crossing of `time` matters, and
            # tying it to "today" implied a timezone that never existed here.
            end = (datetime.combine(date.min, start) + timedelta(minutes=default_minutes)).time()
        booked.append((start, end, name))
    return booked
