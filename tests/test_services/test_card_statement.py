"""Unit tests for the card statement's pure derivations.

Pure-function tests (no database) for :mod:`app.services.card_statement`
(plan step **credit_card:CC-3**).  Every monetary expectation is hand-computed
with the arithmetic shown in a comment.  What is pinned:

* the cycle is CLOSED-OPEN: a day at the close belongs to the next cycle, and
  the statement's valuation date is the day before the close;
* every nominal day clamps through the ONE clamp and never decays: a close
  day of 31 is Jan 31, Feb 28 (29 in a leap year), Mar 31, Apr 30;
* the due date is the first due day strictly AFTER the close (**R-CC26**), so
  a due day later in the month than the close is the SAME month's;
* the sign is NOT pinned here: R-CC29's one flip left this module at plan
  step credit_card:CC-5-5a and its three tests left this file with it at
  CC-5-5b (``tests/test_services/test_liability_sign.py``, beside
  :func:`app.services.liability_sign.owed`);
* the minimum is ``max(floor, round_money(pct x balance))`` clamped to the
  balance and floored at zero, rounded HALF_UP at that one boundary;
* grace is kept when the prior statement was paid in full by its due date,
  and lost one cent short.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.card_statement import (
    CycleWindow,
    cycle_containing,
    cycle_window,
    due_date_for,
    grace_kept,
    minimum_payment,
    statement_sequence,
)
from app.utils.dates import month_ordinal

#: The fixture card's terms: closes on the 20th, due on the 15th.
CLOSE_DAY = 20
DUE_DAY = 15
#: October 2026 as the ordinal ``cycle_window`` takes.
OCT_2026 = month_ordinal(date(2026, 10, 1))


class TestTheCycleWindowIsClosedOpen:
    """``[opens, closes)``: the close day starts the NEXT cycle."""

    def test_the_cycle_closing_in_october_opens_at_septembers_close(self):
        """Close day 20: the October statement is Sep 20 up to Oct 20."""
        assert cycle_window(CLOSE_DAY, OCT_2026) == CycleWindow(
            opens=date(2026, 9, 20), closes=date(2026, 10, 20),
        )

    def test_the_opening_day_is_inside_and_the_close_day_is_not(self):
        """A movement dated at the close belongs to the next statement."""
        window = cycle_window(CLOSE_DAY, OCT_2026)
        assert window.contains(date(2026, 9, 20)) is True
        assert window.contains(date(2026, 10, 19)) is True
        assert window.contains(date(2026, 10, 20)) is False
        assert window.contains(date(2026, 9, 19)) is False

    def test_the_valuation_date_is_the_day_before_the_close(self):
        """The close instant is the END of Oct 19: the seam values a day at
        its end and the cycle excludes its close date."""
        assert cycle_window(CLOSE_DAY, OCT_2026).valuation_date == date(
            2026, 10, 19,
        )

    def test_a_day_before_the_close_is_in_the_cycle_closing_that_month(self):
        """Oct 19 belongs to the statement closing Oct 20."""
        assert cycle_containing(CLOSE_DAY, date(2026, 10, 19)) == CycleWindow(
            opens=date(2026, 9, 20), closes=date(2026, 10, 20),
        )

    def test_the_close_day_itself_is_in_the_next_cycle(self):
        """Oct 20 belongs to the statement closing Nov 20."""
        assert cycle_containing(CLOSE_DAY, date(2026, 10, 20)) == CycleWindow(
            opens=date(2026, 10, 20), closes=date(2026, 11, 20),
        )

    @pytest.mark.parametrize("close_day", range(1, 32))
    def test_every_day_placed_is_contained_by_its_cycle(self, close_day):
        """The placement and the membership test agree on every day of two
        years for every nominal close day -- a leap February (2028) and the
        year boundary included, so the clamped months are pinned facts."""
        day = date(2027, 1, 1)
        while day <= date(2028, 12, 31):
            assert cycle_containing(close_day, day).contains(day) is True
            day = date.fromordinal(day.toordinal() + 1)

    def test_the_year_boundary(self):
        """Dec 25, 2026 and Jan 5, 2027 share the statement closing Jan 20."""
        expected = CycleWindow(opens=date(2026, 12, 20), closes=date(2027, 1, 20))
        assert cycle_containing(CLOSE_DAY, date(2026, 12, 25)) == expected
        assert cycle_containing(CLOSE_DAY, date(2027, 1, 5)) == expected


class TestTheCloseDayClampsAndNeverDecays:
    """A nominal 31 is each month's last day, and April's 30 does not
    become May's (rulings R-PC79 / R-R3, through ``clamped_day``)."""

    @pytest.mark.parametrize(
        ("closing_month", "expected"),
        [
            (date(2026, 2, 1), CycleWindow(date(2026, 1, 31), date(2026, 2, 28))),
            (date(2026, 3, 1), CycleWindow(date(2026, 2, 28), date(2026, 3, 31))),
            (date(2026, 5, 1), CycleWindow(date(2026, 4, 30), date(2026, 5, 31))),
            (date(2028, 2, 1), CycleWindow(date(2028, 1, 31), date(2028, 2, 29))),
            (date(2028, 3, 1), CycleWindow(date(2028, 2, 29), date(2028, 3, 31))),
        ],
        ids=["feb", "mar-after-feb", "may-after-apr", "leap-feb", "mar-after-leap"],
    )
    def test_a_close_day_of_31_is_each_months_last_day(
        self, closing_month, expected,
    ):
        """Feb 28 (29 in 2028), then Mar 31 again: no decay."""
        assert cycle_window(31, month_ordinal(closing_month)) == expected

    def test_february_28_is_the_next_cycles_first_day_for_a_31_close(self):
        """Feb 27 is inside the cycle closing Feb 28; Feb 28 opens the next."""
        assert cycle_containing(31, date(2026, 2, 27)) == CycleWindow(
            date(2026, 1, 31), date(2026, 2, 28),
        )
        assert cycle_containing(31, date(2026, 2, 28)) == CycleWindow(
            date(2026, 2, 28), date(2026, 3, 31),
        )

    def test_a_close_day_of_1(self):
        """The March statement is Feb 1 up to Mar 1."""
        assert cycle_window(1, month_ordinal(date(2026, 3, 1))) == CycleWindow(
            date(2026, 2, 1), date(2026, 3, 1),
        )


class TestTheStatementSequence:
    """Every cycle whose close date lies in the inclusive span, oldest first."""

    def test_three_closes_inside_a_quarter(self):
        """Sep 1 - Nov 30 holds the Sep 20, Oct 20 and Nov 20 closes."""
        assert statement_sequence(
            CLOSE_DAY, date(2026, 9, 1), date(2026, 11, 30),
        ) == [
            CycleWindow(date(2026, 8, 20), date(2026, 9, 20)),
            CycleWindow(date(2026, 9, 20), date(2026, 10, 20)),
            CycleWindow(date(2026, 10, 20), date(2026, 11, 20)),
        ]

    def test_both_ends_are_inclusive(self):
        """A span from one close date to another includes both."""
        closes = [
            w.closes for w in statement_sequence(
                CLOSE_DAY, date(2026, 9, 20), date(2026, 11, 20),
            )
        ]
        assert closes == [date(2026, 9, 20), date(2026, 10, 20), date(2026, 11, 20)]

    def test_a_close_the_day_before_the_span_is_excluded(self):
        """Sep 21 - Nov 20: the Sep 20 close is out, Oct and Nov in."""
        closes = [
            w.closes for w in statement_sequence(
                CLOSE_DAY, date(2026, 9, 21), date(2026, 11, 20),
            )
        ]
        assert closes == [date(2026, 10, 20), date(2026, 11, 20)]

    def test_a_close_the_day_after_the_span_is_excluded(self):
        """Sep 20 - Nov 19: the Nov 20 close is out."""
        closes = [
            w.closes for w in statement_sequence(
                CLOSE_DAY, date(2026, 9, 20), date(2026, 11, 19),
            )
        ]
        assert closes == [date(2026, 9, 20), date(2026, 10, 20)]

    def test_a_span_holding_no_close_is_empty(self):
        """Sep 21 - Oct 19 straddles no close date."""
        assert statement_sequence(
            CLOSE_DAY, date(2026, 9, 21), date(2026, 10, 19),
        ) == []

    def test_a_reversed_span_is_empty(self):
        """``last`` before ``first`` holds nothing."""
        assert statement_sequence(
            CLOSE_DAY, date(2026, 11, 30), date(2026, 9, 1),
        ) == []

    def test_a_31_close_walks_each_months_last_day(self):
        """Jan 31, Feb 28, Mar 31, Apr 30: the clamp per month, no decay."""
        closes = [
            w.closes for w in statement_sequence(
                31, date(2026, 1, 1), date(2026, 4, 30),
            )
        ]
        assert closes == [
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
            date(2026, 4, 30),
        ]


class TestTheDueDateIsTheFirstDueDayAfterTheClose:
    """Ruling R-CC26: the same month when the due day is still ahead of the
    close, the next month otherwise."""

    def test_a_due_day_earlier_in_the_month_is_next_months(self):
        """Closes Sep 20, due on the 15th: Oct 15 (25 days)."""
        assert due_date_for(date(2026, 9, 20), 15) == date(2026, 10, 15)

    def test_a_due_day_later_in_the_month_is_this_months(self):
        """Closes Sep 5, due on the 28th: Sep 28 (23 days), NOT Oct 28."""
        assert due_date_for(date(2026, 9, 5), 28) == date(2026, 9, 28)

    def test_a_due_day_equal_to_the_close_day_is_next_months(self):
        """Strictly after: closes Sep 20, due on the 20th: Oct 20."""
        assert due_date_for(date(2026, 9, 20), 20) == date(2026, 10, 20)

    def test_the_year_boundary(self):
        """Closes Dec 20, 2026, due on the 15th: Jan 15, 2027."""
        assert due_date_for(date(2026, 12, 20), 15) == date(2027, 1, 15)

    def test_a_clamped_due_day_equal_to_a_clamped_close_is_next_months(self):
        """Close day 31 and due day 31 in February both clamp to Feb 28, so
        the due date is Mar 31."""
        assert due_date_for(date(2026, 2, 28), 31) == date(2026, 3, 31)

    def test_the_due_day_clamps_in_its_own_month(self):
        """Closes Jan 31, due on the 30th: Jan 30 is past, so Feb 28."""
        assert due_date_for(date(2026, 1, 31), 30) == date(2026, 2, 28)

    def test_a_due_day_one_day_after_the_close(self):
        """Closes Jan 30, due on the 31st: Jan 31, as the terms state."""
        assert due_date_for(date(2026, 1, 30), 31) == date(2026, 1, 31)


class TestTheMinimumPayment:
    """``max(floor, round_money(pct x balance))`` clamped to the balance and
    floored at zero (design 3.4; R-CC29's numbers)."""

    PERCENT = Decimal("0.0250")
    FLOOR = Decimal("25.00")

    def test_the_percentage_wins_above_the_floor(self):
        """2.5% x 1,234.56 = 30.864 -> 30.86; max(25.00, 30.86) = 30.86."""
        assert minimum_payment(
            Decimal("1234.56"), self.PERCENT, self.FLOOR,
        ) == Decimal("30.86")

    def test_the_floor_wins_below_it(self):
        """2.5% x 400.00 = 10.00; max(25.00, 10.00) = 25.00."""
        assert minimum_payment(
            Decimal("400.00"), self.PERCENT, self.FLOOR,
        ) == Decimal("25.00")

    def test_a_balance_below_the_floor_is_due_whole(self):
        """2.5% x 10.00 = 0.25; max(25.00, 0.25) = 25.00; clamped to 10.00."""
        assert minimum_payment(
            Decimal("10.00"), self.PERCENT, self.FLOOR,
        ) == Decimal("10.00")

    def test_a_zero_balance_owes_nothing(self):
        """max(25.00, 0.00) = 25.00, clamped to max(0.00, 0) = 0.00."""
        assert minimum_payment(
            Decimal("0.00"), self.PERCENT, self.FLOOR,
        ) == Decimal("0.00")

    def test_a_credit_balance_owes_nothing(self):
        """2.5% x -50.00 = -1.25; max(25.00, -1.25) = 25.00; clamped to
        max(-50.00, 0) = 0.00: no minimum on money the issuer owes."""
        assert minimum_payment(
            Decimal("-50.00"), self.PERCENT, self.FLOOR,
        ) == Decimal("0.00")

    @pytest.mark.parametrize(
        ("balance", "expected"),
        [
            # 2.5% x 999.60 = 24.99 < 25.00 floor -> 25.00
            (Decimal("999.60"), Decimal("25.00")),
            # 2.5% x 1000.00 = 25.00 = floor -> 25.00
            (Decimal("1000.00"), Decimal("25.00")),
            # 2.5% x 1000.40 = 25.01 > floor -> 25.01
            (Decimal("1000.40"), Decimal("25.01")),
        ],
        ids=["one-cent-under", "at-the-floor", "one-cent-over"],
    )
    def test_the_floor_crossover(self, balance, expected):
        """The cent where the percentage overtakes the floor."""
        assert minimum_payment(balance, self.PERCENT, self.FLOOR) == expected

    def test_the_percentage_rounds_half_up_at_the_boundary(self):
        """2.5% x 1,234.60 = 30.865 exactly: HALF_UP gives 30.87 (bankers'
        rounding would give 30.86)."""
        assert minimum_payment(
            Decimal("1234.60"), self.PERCENT, self.FLOOR,
        ) == Decimal("30.87")

    def test_a_whole_balance_rule(self):
        """100% with no floor: the minimum IS the balance."""
        assert minimum_payment(
            Decimal("1234.56"), Decimal("1.0000"), Decimal("0.00"),
        ) == Decimal("1234.56")

    def test_no_rule_at_all_owes_nothing(self):
        """0% and a 0.00 floor (both admitted by the CHECKs): 0.00."""
        assert minimum_payment(
            Decimal("1234.56"), Decimal("0.0000"), Decimal("0.00"),
        ) == Decimal("0.00")

    def test_the_result_is_cent_quantized_on_every_arm(self):
        """Percentage, floor, clamp-to-balance, zero AND credit all answer in
        cents -- the credit arm returned the bare ``Decimal("0")`` under
        review, equal to ``0.00`` numerically and one place short."""
        for balance in (
            Decimal("1234.56"), Decimal("400.00"), Decimal("10.00"),
            Decimal("0.00"), Decimal("-50.00"),
        ):
            result = minimum_payment(balance, self.PERCENT, self.FLOOR)
            assert result.as_tuple().exponent == -2, (balance, result)


class TestGraceKept:
    """The prior statement paid in full by its due date keeps grace (R-CC2)."""

    PRIOR = Decimal("1234.56")

    def test_paid_in_full_keeps_grace(self):
        """1,234.56 credited against 1,234.56 owed."""
        assert grace_kept(self.PRIOR, Decimal("1234.56")) is True

    def test_one_cent_short_loses_grace(self):
        """1,234.55 credited against 1,234.56 owed: the control fires."""
        assert grace_kept(self.PRIOR, Decimal("1234.55")) is False

    def test_overpaid_keeps_grace(self):
        """1,300.00 credited against 1,234.56 owed."""
        assert grace_kept(self.PRIOR, Decimal("1300.00")) is True

    def test_nothing_paid_loses_grace(self):
        """0.00 credited against a balance."""
        assert grace_kept(self.PRIOR, Decimal("0.00")) is False

    def test_nothing_owed_keeps_grace_with_nothing_paid(self):
        """A zero statement has nothing to pay by its due date."""
        assert grace_kept(Decimal("0.00"), Decimal("0.00")) is True

    def test_a_credit_statement_keeps_grace(self):
        """A statement the issuer owes on (-50.00) has nothing to pay."""
        assert grace_kept(Decimal("-50.00"), Decimal("0.00")) is True
