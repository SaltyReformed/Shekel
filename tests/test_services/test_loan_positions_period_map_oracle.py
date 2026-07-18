"""C3b2: the positions-based per-period map reproduces the shipping map, every period.

Plan step C3b2 (``docs/audits/balance_architecture/README.md``).  Parallel-runs
the new per-period producer
:func:`app.services.balance_at.positions_period_map` against the map the seam's
AMORTIZING dispatch produces TODAY (:func:`app.services.balance_at.balance_map`,
which routes a configured loan to
:func:`~app.services.net_worth_kernel._build_amortizing_balance_map` --
``net_worth_kernel.py:595-601``) on **EVERY PERIOD** of the shape matrix -- so
step C3b3's map cutover can claim "no money moved" from a proof rather than a
hope (plan Section 7.2).  The mirror of C3a's scalar oracle, one granularity up.

**What this proves, and what it does not.**  ``positions_period_map`` samples
:func:`~app.services.balance_at.positions` -- the fold over SOURCE events for a
begun period, the schedule projection for a future one; ``balance_map`` reads the
sum-of-postings map (:func:`~app.services.loan_posting_service.confirmed_loan_balance_map`)
for begun periods and the SAME schedule projection for future ones, spliced.  So:

* the FUTURE equality is by construction (both route through
  :func:`~app.services.account_projection.forward_balance_at_date` /
  :func:`~app.services.account_projection.compute_forward_loan_period_balance_map`
  over one resolved schedule and seed) -- it proves ``positions_period_map``
  DISPATCHES a future period to the projection at ``period.end``, not the
  projection math (unchanged in this refactor);
* the BEGUN-period equality is the meaningful one -- ``positions_period_map``
  samples the fold where ``balance_map`` reads the postings, and step B2 proves
  those equal on every day (``test_loan_fold_oracle.py``).  This re-confirms it
  THROUGH the period sampling, which is what C3b2 adds over B2: the splice
  boundary (``period.start_date <= ctx.as_of``) and -- the one subtlety B2's
  scalar proof did not cover -- the CURRENT-period clamp to ``ctx.as_of`` (see
  :class:`TestTheCurrentPeriodClampIsLoadBearing`).

The split's VALUE is pinned elsewhere and stays there (B1's hand-computed figures,
the Step-4 reconciliation oracle, A2's Taxes oracle); do not read this file's
equalities as the correctness proof.  They are the equivalence proof C3b3 rests on.

**Sampling is forbidden** (plan Section 7.2): every test walks EVERY period of the
domain, never a sample, and guards the loop so a vacuous domain fails instead of
passing.  Each shape carries a realization assert pinning a value the equality
alone would miss if BOTH producers no-oped (plan Section 7.4), and
:class:`TestThePositionsMapOracleHasTeeth` shows the harness fails on a forced
$1.00 divergence.

**Intact-ledger loans only, by design.**  A BROKEN loan (originated, no OPENING
posting) is the ONE shape the two producers will differ on once wired:
``positions_period_map`` folds the loan's SOURCE facts and answers, where
``balance_map`` RAISES :class:`~app.services.posting_reads.LoanLedgerNotOpenedError`.
That divergence is the deliberate C3b3 behaviour change (the same E1
repairable-cache decision the scalar took at C3b1), pinned by
``test_balance_at.py::TestBrokenLoanFailsLoud``, NOT a mismatch this oracle should
see -- so every loan here is built through the production path that opens its
ledger.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services import balance_at, loan_ledger, loan_loaders
from app.services.balance_at import balance_map, positions_period_map
from app.services.resolution_context import BalanceContext
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    settle_instant_on,
)

# The controlled trued-up loan, shared with B1/B2/C3a so the shapes read one loan
# from several angles: $250,000 originated 2025-01-01 at 6%, trued to $100,000 on
# 2026-01-05.
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)
_ANCHOR_BALANCE = Decimal("100000.00")
_ANCHOR_DATE = date(2026, 1, 5)
_RATE = Decimal("0.06")


def _make_loan(seed_user, db, **kwargs):
    """Build the controlled trued-up loan (origination + a user true-up)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
        rate=_RATE, origination_date=_ORIGINATION_DATE, name="Map Loan",
        **kwargs,
    )


def _settle(seed_user, db, loan, period, amount=Decimal("1000.00")):
    """Settle a Checking -> loan payment, visible from the period's start (C2)."""
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=amount, paid_at=settle_instant_on(period.start_date),
    )


def _assert_positions_map_matches_shipping_every_period(
    loan, ctx, periods, *, min_periods,
):
    """Assert positions_period_map == the shipping map on EVERY period.

    Reads both producers over the SAME period list and asserts zero mismatches.
    Guards the loop so an empty or too-short domain -- which would pass vacuously
    -- fails instead: ``min_periods`` is the floor the caller knows its domain
    must clear (anti-sampling).

    Args:
        loan: The loan account to map.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            (its ``as_of`` is the begun/future boundary both producers splice on).
        periods: The pay periods to key both maps by (the output domain).
        min_periods: The floor on the domain length.
    """
    shipping = balance_map(loan, ctx, periods)
    assert shipping is not None, (
        "balance_map returned None -- the loan has no anchor period, so there is "
        "no shipping map to prove positions_period_map equal to"
    )
    positioned = positions_period_map(loan, ctx, periods)
    mismatches = [
        (period.period_index, str(shipping[period.id]), str(positioned[period.id]))
        for period in periods
        if shipping[period.id] != positioned[period.id]
    ]
    assert not mismatches, (
        f"positions map vs shipping map disagree on {len(mismatches)}/"
        f"{len(periods)} periods (period_index, shipping, positions) "
        f"(first 5): {mismatches[:5]}"
    )
    # Guard the loop: a vacuous or tiny domain proves nothing (anti-sampling).
    assert len(periods) >= min_periods


class TestPositionsMapMatchesShippingMap:
    """positions_period_map == the shipping AMORTIZING map, every period.

    Freezing today at period 6's start puts periods 0-5 in the past (the fold /
    postings begun-period branch), period 6 in the present (the clamp), and
    periods 7-9 in the future (the projection branch) -- so every branch the
    splice chooses between is exercised in one map.
    """

    def test_trued_up_loan_with_payments(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A2's paid shape: origination + true-up + several settled payments.

        Periods 1/3/5 each settle a $1,000 payment (all before the frozen today),
        so the past periods carry real paydown from the $100,000 true-up and the
        future periods amortize on.  Every period matches the shipping map.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            today = seed_periods[6].start_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            positioned = positions_period_map(loan, ctx, seed_periods)
            # Realization (plan Section 7.4): period 0 ends 2026-01-15, after the
            # 2026-01-05 true-up but before the first payment (2026-01-16), so it
            # reads the $100,000 true-up held flat -- not the $250,000 origination
            # principal, and not a paid-down figure.
            assert positioned[seed_periods[0].id] == _ANCHOR_BALANCE
            # The present (period 6) has taken three $1,000 payments: strictly
            # below the true-up, and still owing.
            assert Decimal("0.00") < positioned[seed_periods[6].id] < _ANCHOR_BALANCE
            # The future amortizes further: a later period owes strictly less.
            assert positioned[seed_periods[9].id] < positioned[seed_periods[6].id]

            _assert_positions_map_matches_shipping_every_period(
                loan, ctx, seed_periods, min_periods=10,
            )

    def test_tracking_start_import(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A mid-life import whose tracking-start lands mid-window (C1 plateau).

        Origination opens at $250,000 on 2025-01-01; a tracking-start assertion
        resets it to $100,000 on 2026-02-15 (inside period 3).  Periods 0-2 end
        before that reset, so they read the $250,000 origination principal held
        FLAT -- the honest plateau that replaces the old false pre-opening zero
        (B-11) -- and period 3 onward read the $100,000 reset.  Both the plateau
        and the reset must match the shipping map, period by period.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Import Map Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            tracking_start = date(2026, 2, 15)
            insert_tracking_start_event(
                loan_loaders.load_loan_params(loan.id),
                _ANCHOR_BALANCE, tracking_start,
            )
            db.session.commit()
            today = seed_periods[6].start_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            positioned = positions_period_map(loan, ctx, seed_periods)
            # Realization: period 2 ends 2026-02-12, inside the plateau -- the
            # $250,000 origination principal held flat, never a pre-tracking $0.
            assert positioned[seed_periods[2].id] == _ORIGINATION_PRINCIPAL
            # Period 3 ends 2026-02-26, after the 2026-02-15 reset: the $100,000
            # trued-up balance (no payments recorded, so held flat).
            assert positioned[seed_periods[3].id] == _ANCHOR_BALANCE

            _assert_positions_map_matches_shipping_every_period(
                loan, ctx, seed_periods, min_periods=10,
            )

    def test_payoff_overpayment(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment overpays the balance to zero; later periods hold at zero.

        The loan is trued up to $1,000; a $1,500 payment in period 1 drives it to
        $0 (the ~$495 surplus routes to a Refund leg, not below zero), and a
        second $1,500 payment in period 3 is pure refund.  Period 0 (before the
        payoff payment) still owes the $1,000 true-up; every period from the
        payoff onward is $0 -- and each must match the shipping map.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=Decimal("1000.00"), anchor_date=_ANCHOR_DATE,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
                name="Payoff Map Loan",
            )
            _settle(
                seed_user, db, loan, seed_periods[1], amount=Decimal("1500.00"),
            )
            _settle(
                seed_user, db, loan, seed_periods[3], amount=Decimal("1500.00"),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # Prove the payoff is REALIZED (not a shared no-op): a split routes
            # surplus to a Refund (excess).
            splits = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
            assert any(s.excess > Decimal("0.00") for s in splits)
            today = seed_periods[6].start_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            positioned = positions_period_map(loan, ctx, seed_periods)
            # Realization: period 0 ends 2026-01-15, before the payoff payment
            # (2026-01-16) -- the $1,000 true-up, held flat.
            assert positioned[seed_periods[0].id] == Decimal("1000.00")
            # Period 1 onward: paid off, held at zero (never negative from the
            # overpayment).
            assert positioned[seed_periods[1].id] == Decimal("0.00")
            assert positioned[seed_periods[9].id] == Decimal("0.00")

            _assert_positions_map_matches_shipping_every_period(
                loan, ctx, seed_periods, min_periods=10,
            )

    def test_not_yet_originated_loan(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A loan originating AFTER the frozen today: begun periods are true zeros.

        ``owed_from`` (2026-04-01) is after the NOW (2026-03-13), so every begun
        period owes a TRUE $0.00 -- the loan does not exist yet -- and only the
        periods ending on or after origination carry a balance.  This is the map's
        ``owed_from > ctx.as_of`` branch (the shipping map short-circuits it to a
        true-zero confirmed side; positions routes every date through the
        projection's origination gate), so both must land on the same zeros and the
        same opening balance.
        """
        with app.app_context():
            future_origination = date(2026, 4, 1)
            loan = create_loan_account(
                seed_user, db.session, name="Upcoming Map Mortgage",
                principal=Decimal("200000.00"), rate=_RATE,
                origination_date=future_origination, term=360,
            )
            db.session.commit()
            today = seed_periods[5].start_date  # 2026-03-13, before origination
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            positioned = positions_period_map(loan, ctx, seed_periods)
            # Realization: period 5 ends 2026-03-26, before origination -- a TRUE
            # zero (asked of the ``owed_from`` fact, not inferred from silence).
            assert positioned[seed_periods[5].id] == Decimal("0.00")
            # Period 6 ends 2026-04-09, on/after origination: it owes (near its
            # $200,000 opening, before the first installment lands).
            assert positioned[seed_periods[6].id] > Decimal("0.00")

            _assert_positions_map_matches_shipping_every_period(
                loan, ctx, seed_periods, min_periods=10,
            )


class TestTheCurrentPeriodClampIsLoadBearing:
    """The CURRENT period is clamped to ``ctx.as_of``, and the clamp moves money.

    The one subtlety B2's scalar proof did not cover: a begun period that has not
    ENDED (the current one) must be valued at ``ctx.as_of``, NOT ``period.end``.
    Sampling at ``period.end`` -- a date after the NOW -- would route the current
    period to the forward PROJECTION, paying it down by any installment scheduled
    between the NOW and period end, where the confirmed ledger holds today's
    balance flat.  This shows the two sample points genuinely DIVERGE, so the clamp
    is load-bearing rather than a no-op the equality would pass either way.
    """

    def test_current_period_holds_the_ledger_not_the_projection(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The current period reads the fold at today, and period.end would differ.

        The loan is trued up to $100,000 on 2026-01-05 with NO payments recorded,
        so the fold holds $100,000 flat.  Today is frozen at 2026-02-27 (period
        4's start); period 4 runs to 2026-03-12 and contains the 2026-03-01
        contractual installment.  So:

        * the current period reads the fold at today -- $100,000, held flat, since
          nothing was paid -- matching the shipping map's confirmed side;
        * valuing it at ``period.end`` (2026-03-12) instead would run the forward
          projection through the 2026-03-01 (and overdue 2026-02-01) installments,
          landing STRICTLY BELOW $100,000.

        The strict inequality is the proof the clamp changes the answer.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)  # no payments settled
            db.session.commit()
            today = seed_periods[4].start_date  # 2026-02-27
            current = seed_periods[4]
            assert current.start_date <= today < current.end_date  # begun, unended
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            positioned = positions_period_map(loan, ctx, seed_periods)
            # The current period is the fold at today: $100,000 true-up, held flat
            # (no payment recorded moves it).
            fold_today = balance_at.positions(loan, ctx, [today])[today]
            assert fold_today == _ANCHOR_BALANCE
            assert positioned[current.id] == fold_today

            # Sampling the current period at its END would hand it to the
            # projection, which pays it down through the installments due by
            # 2026-03-12 -- strictly below the clamped value.  That gap is exactly
            # what the clamp prevents leaking into the current period.
            at_period_end = balance_at.positions(
                loan, ctx, [current.end_date],
            )[current.end_date]
            assert at_period_end < positioned[current.id]

            # And the shipping map agrees with the clamped (ledger) value, period
            # for period.
            _assert_positions_map_matches_shipping_every_period(
                loan, ctx, seed_periods, min_periods=10,
            )

    def test_loan_originating_inside_the_current_period_reads_zero(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A loan closing later in the CURRENT period owes 0.00 now, not its opening.

        This is the N-10 shape the read switch was built around, at the period
        map: origination is 2026-03-20, INSIDE the current period (period 5,
        2026-03-13..03-26), but AFTER the frozen today (2026-03-13).  The loan does
        not exist yet, so the current period must read a TRUE $0.00 -- the same
        answer the net-worth hero renders -- and NOT the $200,000 opening.

        The clamp is what makes it so: sampling the current period at its END
        (2026-03-26, on or after origination) would report the full opening
        balance, contradicting the hero on the same day (the very failure the arc
        opened with).  Clamping to today (before origination) folds through the
        origination gate to 0.00.
        """
        with app.app_context():
            origination = date(2026, 3, 20)
            loan = create_loan_account(
                seed_user, db.session, name="Closing Soon Map Loan",
                principal=Decimal("200000.00"), rate=_RATE,
                origination_date=origination, term=360,
            )
            db.session.commit()
            today = seed_periods[5].start_date  # 2026-03-13, before origination
            current = seed_periods[5]
            assert current.start_date <= today < origination <= current.end_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            positioned = positions_period_map(loan, ctx, seed_periods)
            # The current period reads the true zero: the loan has not originated.
            assert positioned[current.id] == Decimal("0.00")

            # Sampling the current period at its END (on/after origination) would
            # report the opening balance instead -- the leak the clamp prevents.
            at_period_end = balance_at.positions(
                loan, ctx, [current.end_date],
            )[current.end_date]
            assert at_period_end == Decimal("200000.00")

            # The shipping map's true-zero confirmed side agrees, every period.
            _assert_positions_map_matches_shipping_every_period(
                loan, ctx, seed_periods, min_periods=10,
            )


class TestThePositionsMapOracleHasTeeth:
    """The harness FAILS on a divergence -- it does not pass vacuously.

    An oracle that compared a value to itself, or walked no periods, would give
    false assurance (the 14-day sample that scored perfect while wrong by
    $178,103.41, plan Section 7.2).  This shows the every-period harness catches a
    forced $1.00 divergence -- the negative control the matrix above rests on.
    """

    def test_a_forced_divergence_makes_the_harness_fail(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Replace the shipping map with one off by $1.00 and the harness raises."""
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            today = seed_periods[3].start_date
            freeze_today(monkeypatch, today)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            real_map = balance_map

            def off_by_a_dollar(account, context, periods, **kwargs):
                shipping = real_map(account, context, periods, **kwargs)
                if shipping is None:
                    return None
                return type(shipping)(
                    (pid, bal + Decimal("1.00")) for pid, bal in shipping.items()
                )

            monkeypatch.setattr(
                "tests.test_services.test_loan_positions_period_map_oracle."
                "balance_map",
                off_by_a_dollar,
            )
            with pytest.raises(AssertionError, match="disagree"):
                _assert_positions_map_matches_shipping_every_period(
                    loan, ctx, seed_periods, min_periods=1,
                )
