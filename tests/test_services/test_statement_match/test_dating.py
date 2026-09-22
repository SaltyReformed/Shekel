"""What the MATCH pane says about WHEN, graded on the value it prints.

:mod:`app.services.statement_match._dating` composes, for one candidate row
against one bank line, the labelled dates a reviewer verifies a proposal BY
and one sentence measuring the bank's posted day from them.  Plan step
``bank_import:X-gz``, ruling **R-BI9**, finding **BI-498**: the pane printed
``settled_on`` bare, a balance true-up stamps one day on every purchase it
settles, and the developer could not verify a correct `$47.61` match by date.

Every row here is built by hand, which is the right grain for a presenter --
the PRODUCER that fills ``period`` and ``purchased_on`` is graded against a
real database in ``test_candidates``.  The worked case is the finding's own:
entry 68, purchased 07-13, budgeted 07-16 .. 07-29, stamped 08-18, against
bank line 303 posted 07-14.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum
from app.services.pay_calendar import DerivedPeriod
from app.services.statement_match._dating import (
    BUDGETED,
    PURCHASED,
    UNDATED,
    DayFact,
    row_days,
)
from app.services.statement_match._offers import BankLine
from app.services.statement_match._subjects import CandidateRow, RowKind
from app.services.statement_match._pairing import (
    days_outside,
    signed_days_outside,
)

#: The paycheck entry 68 is budgeted in.
_PERIOD = DerivedPeriod(
    period_id=9, period_index=8,
    start_date=date(2026, 7, 16), end_date=date(2026, 7, 29),
    end_is_projected=False,
)
#: The day the developer bought the fuel.
_MADE_ON = date(2026, 7, 13)
#: The day the true-up asserted Checking's balance for.
_ASSERTED_FOR = date(2026, 8, 18)


def _line(posted_on, transaction_on=None):
    """Return bank line 303's shape, posted on *posted_on*."""
    return BankLine(
        line_id=303, posted_on=posted_on, amount=Decimal("-47.61"),
        description="POINT OF SALE DEBIT L343 BJS FUEL #9151",
        transaction_on=transaction_on,
    )


def _purchase(settled_on=None, basis=None, period=_PERIOD):
    """Return entry 68 as a candidate, settled as *settled_on* / *basis* say."""
    return CandidateRow(
        version_id=1, kind=RowKind.PURCHASE, row_id=68, label="Gas: Bjs",
        cash_amount=Decimal("-47.61"), settled_on=settled_on,
        is_settled=settled_on is not None, states_own_figure=True,
        parent_id=900, purchased_on=_MADE_ON, period=period,
        settle_day_basis=basis,
    )


def _bill(settled_on=None, basis=None, period=_PERIOD):
    """Return a bill budgeted in *period*, settled as the arguments say."""
    return CandidateRow(
        version_id=1, kind=RowKind.TRANSACTION, row_id=10, label="Electricity",
        cash_amount=Decimal("-180.00"), settled_on=settled_on,
        is_settled=settled_on is not None, states_own_figure=True,
        period=period, settle_day_basis=basis,
    )


class TestThePurchaseTheFindingIsAbout:
    """Entry 68 against line 303, under every settle state it can be in."""

    def test_it_prints_the_purchase_day_and_the_paycheck_and_never_the_stamp(
        self,
    ):
        """The finding's own case: stamped 08-18 by a true-up, bought 07-13.

        The pane said "2026-08-18".  It says ``purchased 07/13`` and
        ``budgeted 07/16 - 07/29`` now, and the stamp is nowhere -- the
        developer's ruling of 2026-09-16 on R-BI9's *not a day to show*.
        """
        days = row_days(
            _purchase(settled_on=_ASSERTED_FOR, basis=SettledDayBasisEnum.ASSERTED),
            _line(date(2026, 7, 14)),
        )

        assert days.facts == (
            DayFact(label=PURCHASED, text="07/13"),
            DayFact(label=BUDGETED, text="07/16 - 07/29"),
        )
        assert "08/18" not in " ".join(fact.text for fact in days.facts)
        assert "08/18" not in days.gap
        assert days.gap == "the bank posted it 1 day after the purchase"

    @pytest.mark.parametrize(
        "settled_on, basis",
        [
            (None, None),
            (_ASSERTED_FOR, SettledDayBasisEnum.ASSERTED),
            (date(2026, 7, 14), SettledDayBasisEnum.OBSERVED),
            (date(2026, 7, 20), SettledDayBasisEnum.ENTERED),
        ],
        ids=["unsettled", "asserted", "observed", "entered"],
    )
    def test_the_settle_state_changes_NOTHING_it_prints(self, settled_on, basis):
        """A settled row and an unsettled one print alike, whatever the basis.

        The ruling's literal reading: whatever the app recorded on the cash
        clock is what accepting rewrites, so it verifies nothing here.
        """
        days = row_days(
            _purchase(settled_on=settled_on, basis=basis),
            _line(date(2026, 7, 14)),
        )

        assert days == row_days(_purchase(), _line(date(2026, 7, 14)))

    def test_the_gap_is_from_the_PURCHASE_DAY_and_not_from_the_window(self):
        """An asserted purchase's window spans 07-13 .. 08-18, inside which the
        bank's 07-14 is "0 days outside" -- and 1 day after the purchase, which
        is what the reviewer asked for."""
        row = _purchase(settled_on=_ASSERTED_FOR, basis=SettledDayBasisEnum.ASSERTED)
        line = _line(date(2026, 7, 14))

        assert days_outside(row.expected_window, line.posted_on) == 0
        assert row_days(row, line).gap == (
            "the bank posted it 1 day after the purchase"
        )

    def test_the_gap_is_measured_from_the_POSTED_day_not_the_made_day(self):
        """The matcher bounds by ``posted_on`` and the match writes it; a
        stated transaction day on the line changes the sentence not at all."""
        stated = _line(date(2026, 7, 14), transaction_on=date(2026, 7, 13))

        assert row_days(_purchase(), stated).gap == (
            "the bank posted it 1 day after the purchase"
        )


class TestTheGapSentenceInEveryDirection:
    """One sentence per side, singular and plural, for both row kinds."""

    @pytest.mark.parametrize(
        "posted_on, sentence",
        [
            (date(2026, 7, 13), "the bank posted it on the purchase day"),
            (date(2026, 7, 14), "the bank posted it 1 day after the purchase"),
            (date(2026, 7, 16), "the bank posted it 3 days after the purchase"),
            (date(2026, 7, 12), "the bank posted it 1 day before the purchase"),
            (date(2026, 7, 1), "the bank posted it 12 days before the purchase"),
        ],
        ids=["same-day", "1-after", "3-after", "1-before", "12-before"],
    )
    def test_a_purchase_is_measured_from_its_day(self, posted_on, sentence):
        """Purchased 07-13; the bank's day walks around it."""
        assert row_days(_purchase(), _line(posted_on)).gap == sentence

    @pytest.mark.parametrize(
        "posted_on, sentence",
        [
            (date(2026, 7, 16), "the bank posted it inside that pay period"),
            (date(2026, 7, 22), "the bank posted it inside that pay period"),
            (date(2026, 7, 29), "the bank posted it inside that pay period"),
            (date(2026, 7, 30), "the bank posted it 1 day after that pay period"),
            (date(2026, 8, 5), "the bank posted it 7 days after that pay period"),
            (date(2026, 7, 15), "the bank posted it 1 day before that pay period"),
            (date(2026, 7, 2), "the bank posted it 14 days before that pay period"),
        ],
        ids=[
            "first-day", "mid", "last-day", "1-after", "7-after", "1-before",
            "14-before",
        ],
    )
    def test_a_bill_is_measured_from_its_whole_period(self, posted_on, sentence):
        """Budgeted 07-16 .. 07-29, both ends inclusive."""
        assert row_days(_bill(), _line(posted_on)).gap == sentence

    def test_a_bill_prints_the_paycheck_and_no_purchase_day(self):
        """A transaction has no purchase day; its one fact is the paycheck."""
        days = row_days(_bill(), _line(date(2026, 7, 22)))

        assert days.facts == (DayFact(label=BUDGETED, text="07/16 - 07/29"),)

    def test_a_settled_bill_prints_exactly_what_an_unsettled_one_does(self):
        """The ruling, on the other row kind: the entered day is not shown."""
        entered = _bill(
            settled_on=date(2026, 7, 20), basis=SettledDayBasisEnum.ENTERED,
        )

        assert row_days(entered, _line(date(2026, 7, 21))) == row_days(
            _bill(), _line(date(2026, 7, 21)),
        )
        assert "07/20" not in row_days(entered, _line(date(2026, 7, 21))).gap

    def test_a_straddling_paycheck_carries_its_years(self):
        """The paycheck register's own year rule, reached and not respelled."""
        straddling = DerivedPeriod(
            period_id=20, period_index=19,
            start_date=date(2026, 12, 24), end_date=date(2027, 1, 6),
            end_is_projected=False,
        )

        days = row_days(_bill(period=straddling), _line(date(2026, 12, 30)))

        assert days.facts == (
            DayFact(label=BUDGETED, text="12/24/26 - 01/06/27"),
        )


class TestARowTheOfferSetCannotProduce:
    """The impossible shapes are stated rather than left to raise."""

    def test_a_purchase_with_no_paycheck_prints_its_day_and_no_placement(self):
        """``period`` is ``None`` only when the calendar lacks the envelope's
        period, which the scope filter makes unreachable; the day still
        prints and the gap is still from it."""
        days = row_days(_purchase(period=None), _line(date(2026, 7, 14)))

        assert days.facts == (DayFact(label=PURCHASED, text="07/13"),)
        assert days.gap == "the bank posted it 1 day after the purchase"

    def test_a_transaction_with_no_paycheck_is_UNDATED_and_prints_nothing(self):
        """Unconstructible through the constructor, which declines the row."""
        days = row_days(_bill(period=None), _line(date(2026, 7, 14)))

        assert days.facts == ()
        assert days.gap == UNDATED


class TestTheSignedDistance:
    """:func:`~._pairing.signed_days_outside` and the magnitude the matcher keeps."""

    _WINDOW = (date(2026, 7, 16), date(2026, 7, 29))

    @pytest.mark.parametrize(
        "day, signed",
        [
            (date(2026, 7, 15), -1),
            (date(2026, 7, 2), -14),
            (date(2026, 7, 16), 0),
            (date(2026, 7, 29), 0),
            (date(2026, 7, 30), 1),
            (date(2026, 8, 12), 14),
        ],
        ids=["1-before", "14-before", "first", "last", "1-after", "14-after"],
    )
    def test_negative_before_zero_inside_positive_after(self, day, signed):
        """The sign is the side; the magnitude is the matcher's own distance."""
        assert signed_days_outside(self._WINDOW, day) == signed
        assert days_outside(self._WINDOW, day) == abs(signed)

    def test_a_point_window_is_its_own_first_and_last(self):
        """A purchase's clock: one day, both ends."""
        point = (date(2026, 7, 13), date(2026, 7, 13))

        assert signed_days_outside(point, date(2026, 7, 13)) == 0
        assert signed_days_outside(point, date(2026, 7, 14)) == 1
        assert signed_days_outside(point, date(2026, 7, 12)) == -1
