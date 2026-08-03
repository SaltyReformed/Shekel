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
# Note: the anchor-history dedupe index keys the STORED ``observed_on``
# column (``app/models/account.py``) since plan step 2, so it needs no
# timezone expression at all and buckets the same civil day this constant
# defines.  It bucketed ``created_at`` by UTC day until then -- an
# independent zone, which is what made two assertions on two Eastern days
# sharing a UTC day collide (finding N-133 / F12).
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


def display_today() -> date:
    """Return the user's wall-clock 'today' in :data:`DISPLAY_TIMEZONE`.

    The calendar date it is right now on the user's clock, for surfaces that
    select a civil WINDOW (a tax/calendar year, a "this month") by the user's
    timezone rather than the server's UTC day.  Storage and the resolver's
    replay boundary stay UTC (``date.today()``); this is the presentation-layer
    "now".

    It keeps a display-tz-attributed figure in the SAME civil year as the
    analytics Taxes tab -- which already derives its year this way
    (``app/routes/analytics.py``) -- around the New Year boundary, when UTC has
    ticked to the next year but the user's Eastern clock has not.  The loan
    page's YTD interest / principal chips are exactly such a figure: their
    producer sums each payment by its display-tz civil paid date (the L9 rule),
    so the year they are summed IN must be the display-tz year to match.

    Returns:
        The current calendar date in :data:`DISPLAY_TIMEZONE`.
    """
    return to_display_tz(datetime.now(timezone.utc)).date()


def utc_instant(instant: datetime) -> datetime:
    """Return *instant* as an aware-UTC ``datetime``.

    The storage convention, applied once: an aware value converts to UTC, a
    naive value is assumed UTC (every ``timestamptz`` in this app is stored
    UTC).  Normalizing every stored instant through this one helper is what
    makes an instant-vs-instant comparison well-defined -- Python refuses to
    compare a naive datetime with an aware one, so a single un-normalized value
    is a ``TypeError`` at read time rather than a wrong answer.

    **It no longer answers "was this settle inside the asserted balance", and
    the paragraph that said it did was the deleted rule's argument** (ruling
    R-DH, 2026-07-31).  That paragraph cited one production pair -- an anchor at
    12:57:08 UTC and two expenses at 13:07 the same day, $108.15 -- as proof
    that the partition must turn on ORDER WITHIN A DAY.  Scored over the whole
    account rather than that one pair, the instant partition cost ``$4,001.42``
    on a single day and ``$40,554.34`` gross across four months, and the day
    rule books a SMALLER correction at that very assertion ($39.27 against
    $68.88).  The question is now answered by comparing two civil days --
    ``CashSourceFact.settled_on`` against ``CashAnchorFact.observed_on``, both
    stored or resolved once -- and the instants this helper normalizes only
    break a tie between two assertions about the SAME day.  Leaving the old
    argument here is how the old rule gets reintroduced, which is why the
    helpers that implemented it were deleted rather than kept.

    Args:
        instant: A stored ``paid_at`` / ``created_at`` / ``asserted_at``
            instant.  Naive values are assumed UTC.

    Returns:
        The aware-UTC equivalent of *instant*.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


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


def attribution_date(
    preferred: date | None, period_start: date, period_end: date,
) -> date:
    """Return the calendar day a pay-period item is attributed to, clamped.

    The single attribution rule shared by the calendar's day-cell grouping
    (``calendar_service``) and the balance-at seam's daily running-balance
    ramp (``balance_resolver.daily_cash_balance_series``), so a flow's cell
    and the balance line's step for it land on the SAME day (the design
    principle "a figure and its caption never disagree").  An item lands on
    ``preferred`` -- its ``due_date`` -- falling back to the pay period's
    ``start_date`` when it has none; the result is then clamped into the
    item's own pay period ``[period_start, period_end]`` span.

    Clamping is load-bearing for the daily balance: every one of a period's
    contributing items must fall on or before the period ``end_date`` so the
    running balance summed through that day equals the period-end balance
    the grid shows (the calendar/grid reconciliation invariant).  A
    ``due_date`` outside the item's own period is possible -- the recurrence
    engine can date an item just outside its period's range, which is why
    the calendar query carries a due-date-in-range OR no-due-date path -- so
    such a stray date is pulled to the nearest period boundary rather than
    escaping onto a neighboring period's day and breaking that period's sum.

    Args:
        preferred: The item's preferred landing date (its ``due_date``), or
            ``None`` to fall back to ``period_start``.
        period_start: The item's pay period inclusive start date (both the
            None fallback and the lower clamp bound).
        period_end: The item's pay period inclusive end date (the upper
            clamp bound).  Assumed ``>= period_start`` (a model CHECK
            constraint enforces ``start_date < end_date``).

    Returns:
        The attributed calendar date, guaranteed within
        ``[period_start, period_end]``.
    """
    landing = preferred if preferred is not None else period_start
    if landing < period_start:
        return period_start
    if landing > period_end:
        return period_end
    return landing
