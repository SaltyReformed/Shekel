"""
Shekel Budget App -- Calendar Date Utilities

Pure date-arithmetic helpers shared across the projection services.  No
Flask, no SQLAlchemy: these operate on :class:`datetime.date` values
only, so they import cleanly into any service or test without the app
stack.
"""
import calendar
from datetime import date


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
