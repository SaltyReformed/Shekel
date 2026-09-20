"""The loan replay over ONE stream: facts, then projections (recurrence:R16-c-1).

Plan step **recurrence:R16-c-1** (ruling **R-R90**) merges a loan's recorded
past and its projected future into one :class:`LoanEventStream`, replayed once
from one seed.  Two rules the merge introduces are observable ONLY on a
hand-built stream on this tree, and each is pinned here rather than left to the
step that first makes it reachable on live data:

* **A RESET clears whatever charges stand before it** (ruling **R-R72** part
  (2)).  Every production charge today is dated where a payment clears it, so
  no charge stands when an assertion lands; ``R16-c-2`` charges every
  contractual installment and a skipped month's charge then meets a true-up.
* **A projection is never placed before a recorded fact**
  (:func:`~app.services.loan_ledger.projection_boundary`).  The recorded
  payments' splits are a function of the facts alone, whether or not the plan
  follows them, which is what lets the posted ledger and a screen agree on
  every settled payment.

Pure: no database, no app context.  Every figure is stated by hand and checked
against :func:`~app.utils.money.accrue_monthly_interest` /
:func:`~app.utils.money.apply_payment_cash`, the ONE accrual and the ONE
allocation.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.loan_ledger import (
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    projection_boundary,
    replay_loan_events,
    replay_loan_stream,
)
from app.utils.money import accrue_monthly_interest
from tests.oracles.loan_monthly_composition import accrual_charge, rate_period

_ZERO = Decimal("0.00")
_RATE = Decimal("0.06")          # $50.00 a month on $10,000.00
_PRINCIPAL = Decimal("10000.00")


def _charge(on_date: date):
    """One hand-stated charge at 6% with no escrow."""
    return accrual_charge(on_date, _RATE, _ZERO)


def _payment(on_date: date, cash: str, *, visible_on: date | None = None):
    """A recorded or projected cash event dated at its installment."""
    return LoanCashEvent(
        on_date=on_date, cash=Decimal(cash), source=None,
        visible_on=visible_on or on_date,
    )


def _reset(on_date: date, balance: str):
    """An assertion of the balance owed on *on_date*."""
    return LoanResetEvent(on_date=on_date, balance=Decimal(balance), source=None)


class TestAResetClearsWhatStands:
    """Ruling R-R72 part (2): an assertion supersedes the charges before it."""

    def test_a_skipped_months_charge_does_not_survive_a_true_up(self):
        """Charge, no payment, true-up, payment: the payment pays pure principal.

        Origination at $10,000.00 on Jan 1; February's charge falls with no
        payment ($50.00 standing); on Feb 15 the owner asserts $9,000.00; the
        March payment of $500.00 then meets NOTHING standing from February --
        the assertion superseded it -- so it pays March's $45.00 on the
        asserted balance and $455.00 of principal.  Were the charge carried
        across, the payment would pay $95.00 of interest and $405.00 of
        principal, which is the second row asserted against.
        """
        stream = LoanEventStream(
            charges=[_charge(date(2026, 2, 1)), _charge(date(2026, 3, 1))],
            payments=[_payment(date(2026, 3, 1), "500.00")],
            resets=[
                _reset(date(2026, 1, 1), "10000.00"),
                _reset(date(2026, 2, 15), "9000.00"),
            ],
        )
        replay = replay_loan_events(_ZERO, stream)
        [outcome] = replay.payments
        march = accrue_monthly_interest(Decimal("9000.00"), _RATE)
        assert march == Decimal("45.00")
        assert outcome.interest == march
        assert outcome.principal == Decimal("455.00")
        assert outcome.balance_after == Decimal("8545.00")
        # The displaced balance a true-up's correction is booked against is the
        # balance JUST BEFORE it -- the standing charge was never in it.
        assert [reset.balance_before for reset in replay.resets] == [
            _ZERO, _PRINCIPAL,
        ]

    def test_the_hypothetical_extra_is_cleared_with_the_charges(self):
        """The what-if extra accumulated before an assertion does not outlive it.

        A projection-only stream (no fact, so the extra accrues at every
        charge): with $100.00 a month, a charge, then a reset, then a charge
        and a payment -- the payment carries ONE helping of extra, March's,
        not February's as well.
        """
        stream = LoanEventStream(
            charges=[_charge(date(2026, 2, 1)), _charge(date(2026, 3, 1))],
            payments=[],
            resets=[
                _reset(date(2026, 1, 1), "10000.00"),
                _reset(date(2026, 2, 15), "9000.00"),
            ],
            projections=[_payment(date(2026, 3, 1), "500.00")],
        )
        [outcome] = replay_loan_events(
            _ZERO, stream, extra_per_period=Decimal("100.00"),
        ).payments
        # 500.00 cash + 100.00 (March's extra only) - 45.00 interest.
        assert outcome.principal == Decimal("555.00")


class TestAProjectionNeverPrecedesAFact:
    """The boundary: the day after the latest recorded payment or assertion."""

    def test_the_boundary_is_the_day_after_the_latest_fact(self):
        """Payments and resets move it; charges do not; no fact means none."""
        facts = LoanEventStream(
            charges=[_charge(date(2026, 12, 1))],
            payments=[_payment(date(2026, 3, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        assert projection_boundary(facts) == date(2026, 3, 2)
        later_reset = LoanEventStream(
            charges=[], payments=facts.payments,
            resets=[*facts.resets, _reset(date(2026, 6, 30), "9000.00")],
        )
        assert projection_boundary(later_reset) == date(2026, 7, 1)
        assert projection_boundary(LoanEventStream(
            charges=[_charge(date(2026, 2, 1))], payments=[], resets=[],
        )) is None

    def test_an_overdue_projection_is_walked_behind_the_facts(self):
        """The Aug installment skipped, Sept paid: the settled split is unmoved.

        Recorded: origination $10,000.00 (Jan 1), charges Aug 1 and Sept 1
        ($50.00 each, the balance untouched between them), a $500.00 payment
        on the Sept 1 installment.  Projected: the overdue Aug 1 row, still
        open.  In contract order the projection would precede the settled
        payment and hand it a different split; the replay keys it at the
        boundary (Sept 2) instead, so the settled payment clears BOTH charges
        ($100.00 interest, $400.00 principal) exactly as it does with no
        projection in the stream, and the projection pays what stands after
        it: nothing, $500.00 of pure principal.
        """
        facts = LoanEventStream(
            charges=[_charge(date(2026, 8, 1)), _charge(date(2026, 9, 1))],
            payments=[_payment(date(2026, 9, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        with_plan = LoanEventStream(
            charges=facts.charges, payments=facts.payments,
            resets=facts.resets,
            projections=[
                _payment(
                    date(2026, 8, 1), "500.00", visible_on=date(2026, 9, 16),
                ),
            ],
        )
        alone = replay_loan_stream(facts)
        merged = replay_loan_stream(with_plan)

        [settled] = alone.payment_splits
        assert (settled.interest, settled.principal) == (
            Decimal("100.00"), Decimal("400.00"),
        )
        # The fact prefix of the merged walk IS the facts-only walk.
        assert merged.settled_splits == alone.payment_splits
        assert merged.anchor_corrections == alone.anchor_corrections
        [projected] = merged.projected_splits
        assert projected.is_projected and not settled.is_projected
        assert projected.due_date == date(2026, 8, 1)
        assert projected.visible_on == date(2026, 9, 16)
        assert (projected.interest, projected.principal) == (
            _ZERO, Decimal("500.00"),
        )
        assert projected.balance_after == Decimal("9100.00")
        # One list, walk order: the fact first, the projection behind it.
        assert merged.payment_splits == [settled, projected]

    def test_a_future_projection_keeps_its_own_date(self):
        """A projection due after the boundary interleaves with the calendar.

        Same facts through Sept; a projection due Oct 1 is keyed at Oct 1, so
        October's charge (dated Oct 1, applied first) stands over it: $48.00
        of interest on the $9,600.00 the September payment left ($500.00 less
        the $100.00 it cleared).
        """
        stream = LoanEventStream(
            charges=[
                _charge(date(2026, 8, 1)), _charge(date(2026, 9, 1)),
                _charge(date(2026, 10, 1)),
            ],
            payments=[_payment(date(2026, 9, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            projections=[_payment(date(2026, 10, 1), "500.00")],
        )
        [_settled, projected] = replay_loan_stream(stream).payment_splits
        assert projected.charge_date == date(2026, 10, 1)
        assert projected.interest == accrue_monthly_interest(
            Decimal("9600.00"), _RATE,
        ) == Decimal("48.00")

    def test_the_extra_accrues_only_behind_the_boundary(self):
        """"An extra $100 a month" never reprices a recorded month.

        Charges Aug 1 and Sept 1 stand before the boundary (the Sept payment
        is the last fact); October's stands behind it.  With $100.00 a month
        the recorded September payment is byte-identical to the no-extra walk,
        and the October projection carries exactly one helping.
        """
        stream = LoanEventStream(
            charges=[
                _charge(date(2026, 8, 1)), _charge(date(2026, 9, 1)),
                _charge(date(2026, 10, 1)),
            ],
            payments=[_payment(date(2026, 9, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            projections=[_payment(date(2026, 10, 1), "500.00")],
        )
        plain = replay_loan_stream(stream)
        topped = replay_loan_stream(stream, extra_per_period=Decimal("100.00"))
        assert topped.settled_splits == plain.settled_splits
        [projected] = topped.projected_splits
        # 500.00 + 100.00 - 48.00 interest on 9,600.00.
        assert projected.principal == Decimal("552.00")


class TestTheOutcomeIsTheRecord:
    """The replay's outcome is read flat, and a chargeless payment names its period."""

    def test_a_payment_no_charge_stands_over_reads_the_calendar_period(self):
        """Ruling R-C's early extra: dated before the first charge, pure principal.

        The stream's ``periods`` supply the governing period; the outcome
        carries it, ``charge_date`` is ``None``, and the cash is principal.
        """
        period = rate_period(_RATE, start_date=date(2026, 1, 1))
        stream = LoanEventStream(
            charges=[_charge(date(2026, 3, 1))],
            payments=[_payment(date(2026, 1, 20), "250.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            periods=[period],
        )
        [early] = replay_loan_stream(stream).payment_splits
        assert early.charge is None and early.charge_date is None
        assert early.period is period
        assert (early.interest, early.principal, early.excess) == (
            _ZERO, Decimal("250.00"), _ZERO,
        )
        assert early.cash == Decimal("250.00")

    def test_a_chargeless_payment_with_no_calendar_is_refused(self):
        """No period to name is an error, never a silent default."""
        stream = LoanEventStream(
            charges=[], payments=[_payment(date(2026, 1, 20), "250.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        with pytest.raises(ValueError, match="non-empty period list"):
            replay_loan_stream(stream)

    def test_the_walk_carries_the_stream_it_replayed(self):
        """A caller holding the walk can replay the same events again."""
        stream = LoanEventStream(
            charges=[_charge(date(2026, 2, 1))],
            payments=[_payment(date(2026, 2, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        walk = replay_loan_stream(stream)
        assert walk.stream is stream
        assert replay_loan_stream(walk.stream).payment_splits == (
            walk.payment_splits
        )
