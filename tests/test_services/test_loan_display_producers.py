"""Tests for the loan-detail display read producers (Loop B rebuild).

:mod:`app.services.loan_posting_service._display` answers the loan detail page's
three measured surfaces from the genesis ledger:

* ``confirmed_loan_principal_in_year`` -- the principal-paid-YTD chip, the
  paid-date sibling of ``confirmed_loan_interest_in_year`` so the two describe one
  set of payments;
* ``confirmed_loan_payment_history`` -- the confirmed payment table, each
  payment's real cash / principal / interest / escrow split; and
* ``loan_balance_anchor_history`` -- the balance-anchors drift scorecard, each
  opening / true-up paired with the ledger's pre-correction balance.

The fixtures are the SAME synthetic split-loan the reconciliation suites use
($250,000 @ 6%, trued up to $100,000): a $100,000 balance at 6% accrues exactly
$500.00 the first month (``100000 * 0.06 / 12``), so every expected figure is
hand-computed and shown in the docstring's arithmetic.  The trueup anchor
($100,000) differs from origination ($250,000), so a correct figure proves the
walk seeds from the trueup.  ``today`` is frozen after the seed window so every
settled payment is confirmed regardless of the wall clock.  All money is
``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.services import loan_posting_service, transfer_service
from app.services.loan_posting_service import LoanAnchorDrift, LoanPaymentHistoryRow
from tests._test_helpers import (
    SPLIT_LOAN,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
)

(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN
_AS_OF = date(2026, 12, 31)
_FROZEN_TODAY = date(2027, 1, 1)
# The seed periods (payment_day=1) are due 02-01 / 03-01 / 04-01 2026, all after
# the 2026-01-10 trueup anchor, so every payment splits from the $100,000 anchor.
_P1_DUE = date(2026, 2, 1)


def _paid_on(year: int, month: int, day: int) -> datetime:
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _make_split_loan(seed_user, db_session, *, escrow_annual=None, name="Split Loan"):
    """Create the synthetic split loan (origination $250k, trued to $100k)."""
    return create_loan_with_trueup(
        seed_user, db_session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE, rate=_RATE,
        origination_date=_ORIGINATION_DATE, escrow_annual=escrow_annual,
        name=name,
    )


def _settle(seed_user, db_session, loan, period, cash, *, paid_at):
    """Settle one Checking -> loan payment (auto-posts its ledger split)."""
    return create_settled_transfer(
        seed_user, db_session, seed_user["account"], loan, period,
        amount=cash, paid_at=paid_at,
    )


# ---------------------------------------------------------------------------
# confirmed_loan_principal_in_year -- the principal-paid-YTD chip
# ---------------------------------------------------------------------------


class TestConfirmedLoanPrincipalReader:
    """The chip reports a loan's ACTUAL principal paid in a year, by paid date.

    Principal is the payment's net on the loan's linked ledger, attributed on the
    SAME paid-date basis as ``confirmed_loan_interest_in_year`` -- so on a $1,000
    on-schedule payment (interest $500, principal $500) the two chips sum to the
    $1,000 cash the payment paid.
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so the settle walk is stable."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_single_payment_principal_by_paid_year(
        self, app, db, seed_user, seed_periods,
    ):
        """One $1,000 payment paid in 2026 reports $500.00 principal for 2026 only.

        interest = round(100000 * 0.005) = 500.00; principal = cash - interest -
        escrow = 1000 - 500 - 0 = 500.00.  Paid 2026-03-15, so 2026 sees 500.00
        and the adjacent years nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), paid_at=_paid_on(2026, 3, 15),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("500.00")
            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("0.00")
            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2027,
            ) == Decimal("0.00")

    def test_running_principal_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three 2026 payments sum their real principal: 500 + 502.50 + 505.01.

        Interest accrues on the shrinking real balance (100000 -> 99500 ->
        98997.50): principal 500.00 / 502.50 / 505.01, so 2026's paid principal is
        1507.51 -- the mirror of the interest reader's 1492.49 (they sum to the
        3000 cash).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    paid_at=_paid_on(2026, 6, 1),
                )
            db.session.commit()

            # 500.00 + 502.50 + 505.01 = 1507.51.
            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("1507.51")

    def test_principal_and_interest_chips_sum_to_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """The two YTD chips together equal the cash of an escrow-free payment.

        One $1,000 payment: principal 500 + interest 500 = 1000 = cash, proving the
        chips describe one set of payments on one basis (no drift between them).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()

            principal = loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2026,
            )
            interest = loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            )
            assert principal + interest == Decimal("1000.00")

    def test_principal_follows_the_paid_date_not_the_pay_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A 2026-period payment PAID in 2025 reports its principal in 2025.

        A period-``_P1`` payment (a 2026 pay period) settled 2025-12-20 attributes
        its 500.00 principal to 2025, matching the interest chip's paid-date rule.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), paid_at=_paid_on(2025, 12, 20),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("500.00")
            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("0.00")

    def test_extra_principal_counts_in_full(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment reports $1,000 principal (the extra lands in principal).

        interest 500.00, escrow 0, principal = 1500 - 500 - 0 = 1000.00 -- the
        real paydown, extra included, not the contractual split.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1500.00"), paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("1000.00")

    def test_unconfigured_loan_returns_none(self, app, db, seed_user):
        """A loan with no OPENING posting reads None (route to the schedule).

        ``create_loan_account`` alone posts no genesis opening (the sync is not
        run), so the chip returns ``None`` -- the interest reader's fallback
        contract -- rather than a misleading $0.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()
            assert loan_posting_service.confirmed_loan_principal_in_year(
                loan.id, seed_user["scenario"].id, 2026,
            ) is None


# ---------------------------------------------------------------------------
# confirmed_loan_payment_history -- the confirmed payment table
# ---------------------------------------------------------------------------


class TestConfirmedLoanPaymentHistory:
    """One row per confirmed payment, split into real cash / P&I / escrow."""

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so every settle is confirmed."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_single_payment_row(self, app, db, seed_user, seed_periods):
        """A $1,000 payment: cash 1000, principal 500, interest 500, no escrow.

        Dated at the true monthly due date (2026-02-01 for period _P1).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert rows == [LoanPaymentHistoryRow(
                due_date=_P1_DUE,
                cash=Decimal("1000.00"),
                principal=Decimal("500.00"),
                interest=Decimal("500.00"),
                escrow=Decimal("0.00"),
            )]

    def test_multi_payment_rows_are_chronological(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments: the shrinking-balance split, cash 1000 each.

        P1 500/500, P2 497.50/502.50, P3 494.99/505.01 (interest/principal).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    paid_at=_paid_on(2026, 6, 1),
                )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [(r.principal, r.interest, r.cash) for r in rows] == [
                (Decimal("500.00"), Decimal("500.00"), Decimal("1000.00")),
                (Decimal("502.50"), Decimal("497.50"), Decimal("1000.00")),
                (Decimal("505.01"), Decimal("494.99"), Decimal("1000.00")),
            ]

    def test_escrow_splits_out_and_cash_reconciles(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment with escrow: cash = principal + interest + escrow exactly.

        Escrow $1,200/yr = $100/mo.  A $1,100 payment: interest 500, escrow 100,
        principal = 1100 - 500 - 100 = 500; and 500 + 500 + 100 = 1100 = cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(
                seed_user, db.session, escrow_annual=Decimal("1200.00"),
            )
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1100.00"), paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()

            [row] = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.cash == Decimal("1100.00")
            assert row.principal == Decimal("500.00")
            assert row.interest == Decimal("500.00")
            assert row.escrow == Decimal("100.00")
            assert row.principal + row.interest + row.escrow == row.cash

    def test_extra_payment_row_shows_the_actual_split(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment: cash 1500, principal 1000, interest 500."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1500.00"), paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()

            [row] = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.cash == Decimal("1500.00")
            assert row.principal == Decimal("1000.00")
            assert row.interest == Decimal("500.00")

    def test_split_matches_the_amortization_history_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The table's split equals the schedule's confirmed rows (same ledger).

        Both surfaces read the same posted legs on the same confirmed cut, so a
        payment's (principal, interest) must be identical between them -- a strong
        cross-check that the table cannot drift from the schedule.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    paid_at=_paid_on(2026, 6, 1),
                )
            db.session.commit()

            table = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            schedule = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert [(r.principal, r.interest) for r in table] == [
                (row.principal, row.interest) for row in schedule
            ]

    def test_payment_in_a_not_yet_begun_period_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled payment whose pay period has not begun by as_of is excluded.

        The table shares the balance readers' 'period has begun' cut: reading as
        of the day BEFORE the payment's period start yields no row; on the start
        it yields one.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            period = seed_periods[_P3]
            _settle(
                seed_user, db.session, loan, period, Decimal("1000.00"),
                paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()

            before = period.start_date - timedelta(days=1)
            assert loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, before,
            ) == []
            assert len(loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, period.start_date,
            )) == 1

    def test_reverted_payment_drops_from_the_table(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled-then-reverted payment leaves no row (its legs net to zero)."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            xfer = _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), paid_at=_paid_on(2026, 6, 1),
            )
            db.session.commit()
            # Revert to Projected through the same chokepoint the route uses.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            ) == []

    def test_unconfigured_loan_returns_none(self, app, db, seed_user):
        """A loan with no OPENING posting reads None (the caller hides the table).

        ``create_loan_account`` posts no genesis opening (the sync is not run), so
        the table returns ``None`` rather than a misleading empty list.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()
            assert loan_posting_service.confirmed_loan_payment_history(
                loan.id, seed_user["scenario"].id, _AS_OF,
            ) is None

    def test_future_as_of_raises(self, app, db, seed_user):
        """A future as_of is a forward projection, out of the reader's domain."""
        with app.app_context():
            loan = _make_split_loan(seed_user, db.session)
            db.session.commit()
            with pytest.raises(ValueError, match="as_of <= today"):
                loan_posting_service.confirmed_loan_payment_history(
                    loan.id, seed_user["scenario"].id, date(2027, 6, 1),
                )


# ---------------------------------------------------------------------------
# loan_balance_anchor_history -- the drift scorecard
# ---------------------------------------------------------------------------


class TestLoanBalanceAnchorHistory:
    """Each anchor paired with the ledger's pre-correction (owed_before) balance."""

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so post-anchor payments settle."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_opening_only_loan_shows_the_opening_row(
        self, app, db, seed_user,
    ):
        """An origination-only loan shows one opening row: computed 0, drift = principal.

        The loan opens from nothing, so ``computed`` is 0.00 and the opening's
        ``drift`` equals the original principal (not a meaningful correction --
        ``is_opening`` flags it for the display).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            ) == [LoanAnchorDrift(
                anchor_date=_ORIGINATION_DATE,
                recorded=Decimal("250000.00"),
                computed=Decimal("0.00"),
                drift=Decimal("250000.00"),
                is_opening=True,
                is_tracking_start=False,
            )]

    def test_opening_and_trueup_drift(self, app, db, seed_user):
        """A trueup with no intervening payments drifts from the un-amortized balance.

        The walk does not auto-amortize between anchors: with no payments between
        origination (2025-01-01, opens 250000) and the trueup (2026-01-10, asserts
        100000), the trueup's ``computed`` is still 250000, so drift = 100000 -
        250000 = -150000 -- the ledger had no payments recorded, so it read high.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            db.session.commit()

            assert loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            ) == [
                LoanAnchorDrift(
                    anchor_date=_ORIGINATION_DATE,
                    recorded=Decimal("250000.00"),
                    computed=Decimal("0.00"),
                    drift=Decimal("250000.00"),
                    is_opening=True,
                    is_tracking_start=False,
                ),
                LoanAnchorDrift(
                    anchor_date=_ANCHOR_DATE,
                    recorded=Decimal("100000.00"),
                    computed=Decimal("250000.00"),
                    drift=Decimal("-150000.00"),
                    is_opening=False,
                    is_tracking_start=False,
                ),
            ]

    def test_post_trueup_payments_do_not_move_the_scorecard(
        self, app, db, seed_user, seed_periods,
    ):
        """Payments after the trueup do not change either anchor's owed_before.

        The drift scorecard measures each anchor against the balance JUST BEFORE
        it; payments due after the trueup (2026-02-01+) sort after it, so the
        trueup's computed stays 250000 -- the scorecard is stable under new
        payments.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    paid_at=_paid_on(2026, 6, 1),
                )
            db.session.commit()

            rows = loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [(r.recorded, r.computed, r.drift) for r in rows] == [
                (Decimal("250000.00"), Decimal("0.00"), Decimal("250000.00")),
                (Decimal("100000.00"), Decimal("250000.00"),
                 Decimal("-150000.00")),
            ]

    def test_unconfigured_loan_returns_none(self, app, db, seed_user):
        """A non-loan account is not a configured loan, so the card is None."""
        with app.app_context():
            assert loan_posting_service.loan_balance_anchor_history(
                seed_user["account"].id, seed_user["scenario"].id, _AS_OF,
            ) is None
