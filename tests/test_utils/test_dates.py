"""Tests for the shared calendar date-arithmetic helpers (deep-hunt #34).

``months_between`` centralizes the "whole calendar months between two
dates, day ignored" convention that was inlined at five financial sites
(escrow inflation, loan amortization remaining/payoff, savings-goal
required-monthly, rate-period numbering).  This file pins the helper's
contract so every caller shares one tested definition:

- The day-of-month is disregarded (only year/month boundaries count).
- The result is signed (``end`` before ``start`` yields a negative).
- Cross-year deltas accumulate 12 per year.
- The inclusive ``+ 1`` and zero-floor that individual callers add stay
  the caller's concern (verified here by composing them with the helper).
"""
from datetime import date, datetime, time, timezone

from app.utils.dates import (
    DISPLAY_TIMEZONE,
    attribution_date,
    display_today,
    months_between,
    to_display_date,
    to_display_tz,
)
from tests._test_helpers import freeze_today


class TestMonthsBetween:
    """Pin the whole-month delta convention shared across the engines."""

    def test_same_month_is_zero_regardless_of_day(self):
        """Two dates in the same calendar month are 0 months apart."""
        # Day ignored: Jan 1 -> Jan 31 still spans no month boundary.
        assert months_between(date(2026, 1, 1), date(2026, 1, 31)) == 0

    def test_one_month_boundary_ignores_day(self):
        """Crossing one month boundary counts as 1, even day 31 -> day 1."""
        # 2026-01-31 -> 2026-02-01: one boundary crossed = 1.
        assert months_between(date(2026, 1, 31), date(2026, 2, 1)) == 1

    def test_full_year_is_twelve(self):
        """Exactly one calendar year is 12 months."""
        assert months_between(date(2026, 1, 1), date(2027, 1, 1)) == 12

    def test_partial_year_day_ignored(self):
        """Mid-month start to start-of-next-year is a whole-month count."""
        # (2027-2026)*12 + (1-1) = 12; the day (15) is disregarded.
        assert months_between(date(2026, 1, 15), date(2027, 1, 1)) == 12

    def test_multi_year_accumulates_twelve_per_year(self):
        """Cross-year deltas add 12 per year plus the month remainder."""
        # (2028-2026)*12 + (3-1) = 26.
        assert months_between(date(2026, 1, 10), date(2028, 3, 31)) == 26

    def test_negative_when_end_before_start(self):
        """An ``end`` earlier than ``start`` yields a negative count."""
        assert months_between(date(2026, 6, 1), date(2026, 1, 1)) == -5

    def test_inclusive_plus_one_composes(self):
        """The payoff site's inclusive ``+ 1`` rides on top of the helper."""
        # amortization payoff uses months_between(...) + 1.
        base = months_between(date(2026, 1, 1), date(2026, 4, 1))
        assert base == 3
        assert base + 1 == 4

    def test_zero_floor_composes(self):
        """The remaining-months floor (max(0, ...)) rides on the helper."""
        # calculate_remaining_months clamps term - elapsed at 0.
        elapsed = months_between(date(2020, 1, 1), date(2026, 1, 1))  # 72
        assert elapsed == 72
        assert max(0, 60 - elapsed) == 0  # past-term loan: no months left


class TestDisplayTimezone:
    """Pin the presentation-layer UTC -> America/New_York conversion.

    Storage stays UTC; these helpers express a stored instant in the
    user's wall clock at the display boundary.  The headline case is the
    motivating bug: a late-evening Eastern true-up whose UTC ``created_at``
    has already rolled to the next calendar day must still display as the
    Eastern day the user performed it.
    """

    def test_summer_instant_converts_to_edt(self):
        """A UTC instant in summer renders in EDT (UTC-4)."""
        # 2026-06-12 14:00 UTC - 4h (EDT) = 2026-06-12 10:00 Eastern.
        utc = datetime(2026, 6, 12, 14, 0, tzinfo=timezone.utc)
        local = to_display_tz(utc)
        assert local.utcoffset().total_seconds() == -4 * 3600
        assert (local.year, local.month, local.day, local.hour) == (
            2026, 6, 12, 10,
        )

    def test_winter_instant_converts_to_est(self):
        """A UTC instant in winter renders in EST (UTC-5), proving DST awareness."""
        # 2026-01-15 14:00 UTC - 5h (EST) = 2026-01-15 09:00 Eastern.
        utc = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
        local = to_display_tz(utc)
        assert local.utcoffset().total_seconds() == -5 * 3600
        assert (local.year, local.month, local.day, local.hour) == (
            2026, 1, 15, 9,
        )

    def test_naive_instant_assumed_utc(self):
        """A naive datetime is treated as UTC, not the server's local zone."""
        # 2026-06-12 14:00 (naive, == UTC) -> 10:00 EDT, same as the aware case.
        local = to_display_tz(datetime(2026, 6, 12, 14, 0))
        assert local.tzinfo == DISPLAY_TIMEZONE
        assert (local.month, local.day, local.hour) == (6, 12, 10)

    def test_display_date_rolls_back_late_evening_summer(self):
        """THE BUG: 9:30 PM EDT true-up stored as next-UTC-day shows the EDT day.

        A true-up at 2026-06-11 21:30 Eastern (EDT, UTC-4) is stored as
        2026-06-12 01:30 UTC.  Truncating the UTC instant gives June 12
        (the old wrong behavior); converting to Eastern first gives the
        June 11 the user actually performed it.
        """
        stored_utc = datetime(2026, 6, 12, 1, 30, tzinfo=timezone.utc)
        assert to_display_date(stored_utc) == date(2026, 6, 11)

    def test_display_date_rolls_back_late_evening_winter(self):
        """Same roll-back holds in winter (EST, UTC-5)."""
        # 2026-01-15 20:00 EST + 5h = 2026-01-16 01:00 UTC -> Eastern Jan 15.
        stored_utc = datetime(2026, 1, 16, 1, 0, tzinfo=timezone.utc)
        assert to_display_date(stored_utc) == date(2026, 1, 15)

    def test_display_date_same_day_when_no_boundary_cross(self):
        """A daytime UTC instant maps to the same calendar day in Eastern."""
        # 2026-06-12 18:00 UTC -> 14:00 EDT: still June 12.
        stored_utc = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
        assert to_display_date(stored_utc) == date(2026, 6, 12)

    def test_display_date_none_passthrough(self):
        """A None instant (anchor never set) returns None, not an error."""
        assert to_display_date(None) is None


class TestDisplayToday:
    """Pin ``display_today`` -- the user's wall-clock 'today' in the display tz.

    The presentation-layer "now" that civil-window surfaces (the analytics Taxes
    tab, the loan YTD chips) select their year / month by, so they follow the
    user's Eastern clock rather than the server's UTC day at the New Year (and
    every late-evening) boundary.
    """

    def test_new_year_boundary_is_the_eastern_day(self, monkeypatch):
        """Midnight UTC on Jan 1 is still Dec 31 in Eastern, so the year is 2026.

        ``at_time=time.min`` pins ``datetime.now(utc)`` to 2027-01-01 00:00
        UTC.  That instant is 7:00 PM EST on 2026-12-31, so ``display_today()``
        is Dec 31 2026 -- year 2026 -- even though ``date.today()`` (UTC) has
        already rolled to 2027.  This is the exact skew the loan YTD chips must
        follow to agree with the Taxes tab.

        The instant is stated rather than inherited: ``freeze_today``'s default
        is NOON, the same instant it pins the database clock to, so that a test
        meaning "today is D" gets D from every reader including the display-tz
        ones.  A test meaning a specific instant asks for one.
        """
        freeze_today(
            monkeypatch, date(2027, 1, 1),
            modules=["app.utils.dates"], at_time=time.min,
        )
        assert display_today() == date(2026, 12, 31)
        assert display_today().year == 2026

    def test_daytime_utc_is_the_same_eastern_day(self, monkeypatch):
        """A mid-day UTC instant maps to the same calendar day in Eastern.

        A manual mid-afternoon stub, independent of ``freeze_today`` entirely:
        2026-06-12 18:00 UTC is 14:00 EDT, still June 12, so
        ``display_today()`` is 2026-06-12.  ``freeze_today`` would also serve
        now that its default is NOON; the hand-rolled stub predates that and is
        kept because pinning the instant explicitly is what this case is about.
        """
        class _FixedDateTime(datetime):
            """A datetime whose ``now`` is a fixed mid-day UTC instant."""

            @classmethod
            def now(cls, tz=None):
                """Return 2026-06-12 18:00 in the requested tz (UTC by default)."""
                fixed = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr("app.utils.dates.datetime", _FixedDateTime)
        assert display_today() == date(2026, 6, 12)


class TestAttributionDate:
    """Pin the shared calendar-attribution clamp.

    ``attribution_date`` is the one rule both the calendar's day-cell
    grouping and the daily balance ramp use, so a flow's cell and the
    balance line's step land on the same day.  An item lands on its
    ``due_date`` (fallback: the period ``start_date``), clamped into the
    period's inclusive span so a period's flows always sum by its
    ``end_date`` -- the calendar/grid reconciliation invariant.
    """

    def test_due_date_within_period_is_unchanged(self):
        """An in-period due_date is the attribution day verbatim."""
        assert attribution_date(
            date(2026, 7, 6), date(2026, 7, 1), date(2026, 7, 14),
        ) == date(2026, 7, 6)

    def test_missing_due_date_falls_back_to_period_start(self):
        """A None due_date attributes to the period start_date."""
        assert attribution_date(
            None, date(2026, 7, 1), date(2026, 7, 14),
        ) == date(2026, 7, 1)

    def test_due_date_before_period_clamps_to_start(self):
        """A due_date before the period start is pulled up to start_date."""
        # Recurrence engine dated the item Jun 28; its period is Jul 1-14,
        # so it lands on Jul 1 rather than escaping into June.
        assert attribution_date(
            date(2026, 6, 28), date(2026, 7, 1), date(2026, 7, 14),
        ) == date(2026, 7, 1)

    def test_due_date_after_period_clamps_to_end(self):
        """A due_date after the period end is pulled down to end_date.

        An item in the Jul 1-14 period dated Jul 20 lands on Jul 14 so the
        period's running-balance sum still closes by its end_date and
        reconciles with the grid period-end.
        """
        assert attribution_date(
            date(2026, 7, 20), date(2026, 7, 1), date(2026, 7, 14),
        ) == date(2026, 7, 14)

    def test_boundary_dates_are_inclusive(self):
        """Both period endpoints are valid landing days (inclusive clamp)."""
        assert attribution_date(
            date(2026, 7, 1), date(2026, 7, 1), date(2026, 7, 14),
        ) == date(2026, 7, 1)
        assert attribution_date(
            date(2026, 7, 14), date(2026, 7, 1), date(2026, 7, 14),
        ) == date(2026, 7, 14)
