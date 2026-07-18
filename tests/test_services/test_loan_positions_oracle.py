"""C3a: positions() reproduces the shipping scalar on EVERY day, past AND future.

Plan step C3a (``docs/audits/balance_architecture/README.md``).  Parallel-runs
the new total loan producer :func:`app.services.balance_at.positions` against the
scalar the seam's AMORTIZING dispatch reads TODAY
(:func:`app.services.net_worth_kernel.amortizing_balance_at`) on every day of a
domain that spans BOTH sides of the resolver's NOW -- so step C3b's cutover can
claim "no money moved" from a proof rather than a hope (plan Section 7.2).

**What this proves, and what it does not.**  ``positions`` reads the FOLD for the
past and the schedule projection for the future; the scalar reads the sum-of-postings
reader for the past and the SAME schedule projection for the future.  So:

* the FUTURE equality is by construction (both call
  :func:`app.services.account_projection.forward_balance_at_date` with the same
  resolved schedule and seed) -- it proves ``positions`` DISPATCHES a future date to
  the projection, with the right seed and origination gate, NOT the projection math
  (which does not change in this refactor);
* the PAST equality is the meaningful one -- ``positions`` reads the fold over source
  events where the scalar reads the postings, and step B2 proves those equal
  (``test_loan_fold_oracle.py``).  This walk re-confirms it THROUGH the ``positions``
  dispatch, catching a boundary, seed, or not-originated bug the scalar's per-date
  branch would not.

The split's VALUE is pinned elsewhere and stays there (B1's hand-computed figures,
the Step-4 reconciliation oracle, A2's Taxes oracle); do not read this file's
equalities as the correctness proof.  They are the equivalence proof C3b rests on.

**Sampling is forbidden** (plan Section 7.2): every test walks EVERY day of its
domain, never a sample, and guards the loop so a vacuous domain fails instead of
passing.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import balance_at
from app.services.net_worth_kernel import amortizing_balance_at
from app.services.resolution_context import BalanceContext
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    settle_instant_on,
)

# The controlled trued-up loan, shared with B1/B2 so the three read one shape from
# three angles: $250,000 originated 2025-01-01 at 6%, trued to $100,000 on
# 2026-01-05.
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)
_ANCHOR_BALANCE = Decimal("100000.00")
_ANCHOR_DATE = date(2026, 1, 5)
_RATE = Decimal("0.06")

# A date before every event (origination included), so each domain also pins the
# pre-origination 0.00 -- truly "no debt" -- both producers must agree on.
_BEFORE_ALL = date(2024, 12, 31)


def _make_loan(seed_user, db, **kwargs):
    """Build the controlled trued-up loan (origination + a user true-up)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
        rate=_RATE, origination_date=_ORIGINATION_DATE, name="Positions Loan",
        **kwargs,
    )


def _settle(seed_user, db, loan, period, amount=Decimal("1000.00")):
    """Settle a Checking -> loan payment, visible from the period's start (C2)."""
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=amount, paid_at=settle_instant_on(period.start_date),
    )


def _assert_positions_match_scalar_every_day(
    account, ctx, start, end, *, min_days,
):
    """Assert positions() == the shipping scalar on EVERY day of ``[start, end]``.

    ``positions`` folds the whole domain in ONE call (past dates cost one fold
    walk, future dates one schedule read each); the scalar answers per day.  A
    guard fails a vacuous or too-short domain rather than passing it (anti-sampling).

    Args:
        account: The loan account to value.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            (its ``as_of`` is the past/future boundary both producers split on).
        start: First day of the domain (inclusive).
        end: Last day of the domain (inclusive).
        min_days: The floor on the domain length -- proves the walk is not a
            handful of days agreeing.
    """
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    positioned = balance_at.positions(account, ctx, days)
    mismatches = [
        (day.isoformat(), str(positioned[day]), str(scalar))
        for day in days
        if (scalar := amortizing_balance_at(account, ctx, day))
        != positioned[day]
    ]
    assert not mismatches, (
        f"positions vs scalar disagree on {len(mismatches)}/{len(days)} "
        f"days (first 5): {mismatches[:5]}"
    )
    # Guard the loop: a vacuous or tiny domain proves nothing (anti-sampling).
    assert days[0] == start and days[-1] == end
    assert len(days) >= min_days


class TestPositionsMatchesScalar:
    """positions() == the shipping AMORTIZING scalar, every day, both sides of NOW.

    Freezing today MID-domain is the point: days before it exercise the fold
    branch, days after it the projection branch, and the boundary itself the
    dispatch that step C3b will adopt.
    """

    def test_trued_up_loan_across_the_now_boundary(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A trued-up loan with settled payments, walked past AND future.

        Payments settle in periods 1 and 3 (both before the frozen today); the
        walk spans the pre-origination zero, the origination-to-true-up plateau,
        the paid-down present, and the projected future -- every day matching the
        scalar the seam reads now.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            # Freeze today mid-run (period 5 start), so periods 1/3 are settled
            # past and periods 6-9 are future.
            today = seed_periods[5].start_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            # Realization asserts: pin the VALUES the equality would miss if BOTH
            # producers no-oped (plan Section 7.4).
            positioned = balance_at.positions(
                loan, ctx,
                [_BEFORE_ALL, date(2025, 6, 1), today, seed_periods[9].start_date],
            )
            # Before origination: no debt.
            assert positioned[_BEFORE_ALL] == Decimal("0.00")
            # Origination-to-true-up plateau: the $250,000 principal held FLAT
            # (the fold's honest plateau, not a pre-tracking $0).
            assert positioned[date(2025, 6, 1)] == _ORIGINATION_PRINCIPAL
            # Two $1,000 payments have paid the $100,000 true-up down, so today is
            # below it (the fold books real principal).
            assert positioned[today] < _ANCHOR_BALANCE
            # The future projection amortizes further: a later future date owes
            # strictly less than today (installments on 04-01 and 05-01 land in
            # the projected window).
            assert positioned[seed_periods[9].start_date] < positioned[today]

            _assert_positions_match_scalar_every_day(
                loan, ctx, _BEFORE_ALL, seed_periods[9].start_date,
                min_days=490,
            )

    def test_not_yet_originated_loan_is_all_projection(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A loan originating AFTER the frozen today: its whole timeline projects.

        ``owed_from`` is after the NOW, so every date -- even a past one -- reads
        the projection: ``0.00`` before origination (the not-borrowed zero, asked
        of the FACT), its opening balance forward.  The scalar's third dispatch
        case, reproduced by positions() with the opening seed.
        """
        with app.app_context():
            future_origination = date(2026, 4, 1)
            loan = create_loan_account(
                seed_user, db.session, name="Upcoming Mortgage",
                principal=Decimal("200000.00"), rate=_RATE,
                origination_date=future_origination, term=360,
            )
            db.session.commit()
            today = seed_periods[5].start_date  # 2026-03-13, before origination
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            # Realization: the not-borrowed zero, then the opening jump.
            positioned = balance_at.positions(
                loan, ctx,
                [future_origination - timedelta(days=1), future_origination],
            )
            assert positioned[future_origination - timedelta(days=1)] == (
                Decimal("0.00")
            )
            # The day it closes it owes its full opening principal (seeded from the
            # opening anchor, not from a resolver current_balance of 0.00).
            assert positioned[future_origination] == Decimal("200000.00")

            _assert_positions_match_scalar_every_day(
                loan, ctx, date(2026, 2, 1), date(2026, 6, 1), min_days=120,
            )


class TestThePositionsOracleHasTeeth:
    """The harness FAILS on a divergence -- it does not pass vacuously.

    An oracle that compared a value to itself, or walked no days, would give
    false assurance (the 14-day sample that scored perfect while wrong by
    $178,103.41, plan Section 7.2).  This shows the every-day harness catches a
    forced $1.00 divergence -- the negative control the matrix above rests on.
    """

    def test_a_forced_divergence_makes_the_harness_fail(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Replace the scalar with one off by $1.00 and the harness raises."""
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            today = seed_periods[3].start_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            real_scalar = amortizing_balance_at

            def off_by_a_dollar(account, context, as_of):
                return real_scalar(account, context, as_of) + Decimal("1.00")

            monkeypatch.setattr(
                "tests.test_services.test_loan_positions_oracle."
                "amortizing_balance_at",
                off_by_a_dollar,
            )
            with pytest.raises(AssertionError, match="disagree"):
                _assert_positions_match_scalar_every_day(
                    loan, ctx, _BEFORE_ALL, today, min_days=1,
                )
