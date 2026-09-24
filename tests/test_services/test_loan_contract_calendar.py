"""A loan's ONE charge calendar: the contract's installments, through a stream's last event.

Plan step **recurrence:R16-c-2** (rulings **R-R72**, **R-R89** and **R-R100**).
A loan is charged on EVERY contractual installment from origination, whether or
not a payment lands on it, in the posted ledger's walk and a screen's alike:
:func:`~app.services.loan_ledger.installment_dates` is the one date producer,
:func:`~app.services.loan_ledger._charges.contract_charges` prices each date, and
:func:`~app.services.loan_ledger.with_contract_charges` is the one composer
that decides how far a stream is charged -- through its LAST event, a payment,
an assertion or a projection alike.

Pure: no database, no app context.  Every figure is stated by hand and checked
against :func:`~app.utils.money.accrue_monthly_interest` /
:func:`~app.utils.money.apply_payment_cash`, the ONE accrual and the ONE
allocation (6% on $10,000.00 is $50.00 a month).
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.services.loan_ledger import (
    LoanCalendar,
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    installment_dates,
    replay_loan_stream,
    with_contract_charges,
)
from app.services.loan_ledger._charges import contract_charges
from tests.oracles.loan_monthly_composition import accrual_charge, rate_period

_ZERO = Decimal("0.00")
_RATE = Decimal("0.06")          # $50.00 a month on $10,000.00


def _calendar(
    origination: date = date(2026, 1, 1),
    payment_day: int = 1,
    periods=None,
) -> LoanCalendar:
    """A loan's contract terms: one 6% rate period and no escrow unless stated."""
    return LoanCalendar(
        origination_date=origination,
        payment_day=payment_day,
        periods=periods if periods is not None else [
            rate_period(_RATE, start_date=origination),
        ],
        escrow_lines=[],
    )


def _payment(on_date: date, cash: str) -> LoanCashEvent:
    """A cash event dated at its installment, visible the same day."""
    return LoanCashEvent(
        on_date=on_date, cash=Decimal(cash), source=None, visible_on=on_date,
    )


def _reset(on_date: date, balance: str, *, opening: bool = False):
    """An asserted balance."""
    return LoanResetEvent(
        on_date=on_date, balance=Decimal(balance), source=None,
        is_opening=opening,
    )


class TestInstallmentDates:
    """The one date producer: first installment the month after origination, monthly after."""

    def test_the_first_installment_is_the_month_after_origination(self):
        """Originated mid-January, due the 1st: February 1st, March 1st."""
        assert installment_dates(date(2026, 1, 15), 1, date(2026, 3, 1)) == [
            date(2026, 2, 1), date(2026, 3, 1),
        ]

    def test_a_through_before_the_first_installment_yields_nothing(self):
        """Nothing is due in the origination's own month."""
        assert installment_dates(date(2026, 1, 1), 1, date(2026, 1, 31)) == []

    def test_a_due_day_of_31_returns_to_the_31st_after_february(self):
        """Clamped to Feb 28 in 2027, then back to the 31st -- never stuck on the 28th."""
        assert installment_dates(date(2026, 12, 31), 31, date(2027, 4, 30)) == [
            date(2027, 1, 31), date(2027, 2, 28),
            date(2027, 3, 31), date(2027, 4, 30),
        ]

    def test_a_leap_february_clamps_to_the_29th(self):
        """2028 is a leap year: the 31st clamps to Feb 29, then returns."""
        assert installment_dates(date(2027, 12, 31), 31, date(2028, 3, 31)) == [
            date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31),
        ]

    def test_a_due_day_of_30_returns_to_the_30th_after_february(self):
        """Feb 28, then Mar 30 -- the day is clamped to each month afresh."""
        assert installment_dates(date(2026, 1, 30), 30, date(2026, 3, 30)) == [
            date(2026, 2, 28), date(2026, 3, 30),
        ]


class TestContractCharges:
    """Each installment is priced on its OWN date (ruling D5)."""

    def test_each_charge_takes_the_rate_in_force_on_its_installment(self):
        """A step to 12% on 03-01 reprices March onward, never February."""
        periods = [
            rate_period(_RATE, start_date=date(2026, 1, 1)),
            replace(
                rate_period(Decimal("0.12"), start_date=date(2026, 3, 1)),
                index=1,
            ),
        ]
        charges = contract_charges(
            _calendar(periods=periods), date(2026, 4, 1),
        )
        assert [(c.on_date, c.period.annual_rate, c.escrow) for c in charges] == [
            (date(2026, 2, 1), _RATE, _ZERO),
            (date(2026, 3, 1), Decimal("0.12"), _ZERO),
            (date(2026, 4, 1), Decimal("0.12"), _ZERO),
        ]

    def test_a_through_before_the_first_installment_charges_nothing(self):
        """No installment has fallen, so nothing is owed yet."""
        assert contract_charges(_calendar(), date(2026, 1, 31)) == []


class TestWithContractCharges:
    """The one composer: charges through the stream's LAST event, of any kind."""

    def test_the_charges_reach_the_last_projection(self):
        """A projection in June is faced by June's installment."""
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 2, 1), "500.00")],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
                projections=[_payment(date(2026, 6, 10), "500.00")],
            ),
            _calendar(),
        )
        assert [c.on_date for c in stream.charges] == [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
            date(2026, 5, 1), date(2026, 6, 1),
        ]

    def test_the_charges_reach_a_last_assertion(self):
        """An assertion after the last payment still bounds the calendar."""
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 2, 1), "500.00")],
                resets=[
                    _reset(date(2026, 1, 1), "10000.00", opening=True),
                    _reset(date(2026, 4, 15), "9000.00"),
                ],
            ),
            _calendar(),
        )
        assert [c.on_date for c in stream.charges][-1] == date(2026, 4, 1)

    def test_a_stream_with_no_event_is_charged_nothing(self):
        """No event, no reach: the composer charges nothing."""
        assert with_contract_charges(
            LoanEventStream(charges=(), payments=()), _calendar(),
        ).charges == ()

    def test_the_stream_carries_the_calendar_it_was_charged_on(self):
        """The stream keeps the calendar object, so a reader takes its terms from it."""
        calendar = _calendar()
        stream = with_contract_charges(
            LoanEventStream(
                charges=(), payments=[_payment(date(2026, 2, 1), "500.00")],
            ),
            calendar,
        )
        assert stream.calendar is calendar


class TestTheReplayOverTheContractsCalendar:
    """What charging every installment does to a walk (rulings R-R72, R-R89)."""

    def test_a_skipped_month_is_cleared_by_the_next_payment_first(self):
        """February paid, March skipped, April paid: April clears March and April.

        $10,000.00 at 6%: Feb 50.00 / 450.00 -> 9,550.00; March charges 47.75
        and nothing pays; April charges 47.75 more on the unreduced 9,550.00,
        so April's $500.00 clears 95.50 and pays 404.50 -> 9,145.50.
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[
                    _payment(date(2026, 2, 1), "500.00"),
                    _payment(date(2026, 4, 1), "500.00"),
                ],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
            ),
            _calendar(),
        ))
        february, april = walk.settled_splits
        assert (february.interest, february.principal) == (
            Decimal("50.00"), Decimal("450.00"),
        )
        assert (april.interest, april.principal, april.balance_after) == (
            Decimal("95.50"), Decimal("404.50"), Decimal("9145.50"),
        )
        assert april.charge_date == date(2026, 4, 1)

    def test_an_assertion_clears_the_months_standing_before_it(self):
        """Originated 2025, never paid, asserted $10,000.00 on 2026-01-10.

        Twelve installments (2025-02-01 .. 2026-01-01) stand when the
        assertion lands; it states the balance owed and clears them (R-R72
        part (2)), so the February payment faces February's $50.00 alone.
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 2, 1), "500.00")],
                resets=[
                    _reset(date(2025, 1, 1), "12000.00", opening=True),
                    _reset(date(2026, 1, 10), "10000.00"),
                ],
            ),
            _calendar(origination=date(2025, 1, 1)),
        ))
        assert len([
            c for c in walk.stream.charges if c.on_date < date(2026, 1, 10)
        ]) == 12
        [february] = walk.settled_splits
        assert (february.interest, february.principal) == (
            Decimal("50.00"), Decimal("450.00"),
        )

    def test_an_off_day_payment_faces_the_installment_its_interval_opens_on(self):
        """Due the 22nd; a payment on 03-10 falls in the Feb 22 - Mar 21 interval.

        It faces February 22nd's charge -- one month, $50.00 -- and never
        March 22nd's, which has not fallen (ruling R-R89, finding D55: the
        calendar month would have paired it with March).
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 3, 10), "500.00")],
                resets=[_reset(date(2026, 1, 22), "10000.00", opening=True)],
            ),
            _calendar(origination=date(2026, 1, 22), payment_day=22),
        ))
        [payment] = walk.settled_splits
        assert payment.charge_date == date(2026, 2, 22)
        assert (payment.interest, payment.principal) == (
            Decimal("50.00"), Decimal("450.00"),
        )

    def test_the_what_if_extra_rides_no_month_skipped_behind_a_fact(self):
        """An extra $100 a month joins the charges after the facts only.

        Facts: opening $10,000.00 on Jan 1, a $500.00 payment on June 1.
        Projected: April's overdue catch-up (pushed behind June) and July's
        installment.  February through June are the facts' months, cleared
        by June's payment; the extra accrues at July's charge alone, so the
        catch-up is $500.00 of principal with or without it and July carries
        exactly one $100.00 helping.
        """
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 6, 1), "500.00")],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
                projections=[
                    _payment(date(2026, 4, 1), "500.00"),
                    _payment(date(2026, 7, 1), "500.00"),
                ],
            ),
            _calendar(),
        )
        plain = replay_loan_stream(stream)
        topped = replay_loan_stream(stream, extra_per_period=Decimal("100.00"))
        assert topped.settled_splits == plain.settled_splits
        catch_up, july = topped.projected_splits
        plain_catch_up, plain_july = plain.projected_splits
        assert catch_up.principal == plain_catch_up.principal == Decimal("500.00")
        assert july.principal - plain_july.principal == Decimal("100.00")

    def test_a_hand_stated_charge_list_is_replaced_by_the_calendar(self):
        """The composer owns the charges: a stream's own list does not survive."""
        stream = with_contract_charges(
            LoanEventStream(
                charges=[accrual_charge(date(2026, 2, 15), _RATE, _ZERO)],
                payments=[_payment(date(2026, 2, 1), "500.00")],
            ),
            _calendar(),
        )
        assert [c.on_date for c in stream.charges] == [date(2026, 2, 1)]
