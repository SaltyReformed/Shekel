"""
Shekel Budget App -- Calendar Date Utilities

Pure date-arithmetic helpers shared across the projection services, plus
the presentation-layer timezone conversion used to render stored UTC
instants in the user's wall clock.  No Flask, no SQLAlchemy: these
operate on :class:`datetime.date` / :class:`datetime.datetime` values
only, so they import cleanly into any service or test without the app
stack.
"""
import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

# Single source of truth for the timezone the UI presents instants in.
# Storage and every backend computation stay UTC (each ``timestamptz``
# column is stored UTC by ``CreatedAtMixin``/``TimestampMixin``); this
# constant governs DISPLAY only, converting a stored UTC instant to the
# user's wall clock at the presentation boundary.  ``America/New_York``
# is a DST-aware zone, so the rendered clock is EDT (UTC-4) in summer and
# EST (UTC-5) in winter rather than a wrong fixed offset.
#
# Note: the anchor-history dedupe index buckets ``created_at`` by UTC day
# in SQL (``app/models/account.py``); that IMMUTABLE expression cannot
# reference this constant and is deliberately independent of the display
# zone -- it is an internal same-day-double-submit guard, not a user
# surface.
DISPLAY_TIMEZONE = ZoneInfo("America/New_York")


def to_display_tz(value: datetime) -> datetime:
    """Convert a stored UTC instant to the UI display timezone.

    Presentation-only (E-16 sibling of ``to_percent``): the database and
    all backend logic operate in UTC; this is the boundary that expresses
    a stored instant in the user's wall clock (:data:`DISPLAY_TIMEZONE`).

    A naive ``value`` is assumed to be UTC -- every ``timestamptz`` in
    this app is stored UTC, but a value that has lost its tzinfo (e.g. a
    naive test fixture) would otherwise be interpreted in the server's
    local zone by ``astimezone``, silently shifting the rendered day.

    Args:
        value: A timezone-aware (or naive-assumed-UTC) datetime.

    Returns:
        The same instant expressed in :data:`DISPLAY_TIMEZONE`.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE)


def to_display_date(value: datetime | None) -> date | None:
    """Return the calendar date of a stored UTC instant in display tz.

    The day-of-record the user sees: the instant converted to
    :data:`DISPLAY_TIMEZONE` first, then truncated to a date, so a
    late-evening Eastern event does not roll onto the next UTC day.
    ``None``-safe so callers can pass an absent timestamp (e.g. an anchor
    that has never been set) straight through.

    Args:
        value: A stored UTC instant, or ``None``.

    Returns:
        The display-timezone calendar date, or ``None`` when ``value`` is
        ``None``.
    """
    if value is None:
        return None
    return to_display_tz(value).date()


def add_months(start: date, months: int) -> date:
    """Add ``months`` calendar months to ``start``, day-clamped.

    The result's day is clamped to the target month's last day, so
    ``add_months(date(2026, 1, 31), 1)`` yields ``date(2026, 2, 28)``
    rather than raising for the nonexistent February 31st.

    Overflow guard: returns :attr:`datetime.date.max` when the result
    would exceed year 9999 (Python's maximum representable year) instead
    of raising, so a long projection horizon degrades gracefully to a
    sentinel far-future date.

    Args:
        start: The starting date.
        months: Number of months to add (non-negative).

    Returns:
        A new :class:`datetime.date` ``months`` months after ``start``,
        or :attr:`datetime.date.max` on year-9999 overflow.
    """
    total_months = start.month - 1 + months
    year = start.year + total_months // 12
    month = total_months % 12 + 1

    if year > 9999:
        return date.max

    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def months_between(start: date, end: date) -> int:
    """Return whole calendar months from ``start`` to ``end`` (day ignored).

    Computes ``(end.year - start.year) * 12 + (end.month - start.month)``:
    the number of month boundaries between the two dates, with the
    day-of-month disregarded.  The delta from 2026-01-15 to 2027-01-01 is
    12, and from 2026-01-31 to 2026-02-01 is 1.

    The result is signed and unclamped -- an ``end`` before ``start``
    yields a negative count.  Callers that need a floor (e.g. "months
    remaining cannot drop below zero") or an inclusive ``+ 1`` clamp or
    adjust at the call site, because the bound differs per caller.

    Args:
        start: The earlier reference date.
        end: The later reference date.

    Returns:
        The signed whole-month delta as an ``int``.
    """
    return (end.year - start.year) * 12 + (end.month - start.month)
