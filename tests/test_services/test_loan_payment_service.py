"""
Tests for the loan payment service.

Verifies that get_payment_history() correctly queries shadow income
transactions, applies the right filters, uses effective_amount for
the payment amount, and maps is_confirmed from status.is_settled.

Also tests the payment preparation utilities (compute_contractual_pi
and prepare_payments_for_engine) that correct escrow inflation and
biweekly month overlaps before passing payments to the amortization
engine.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.services.amortization_engine import PaymentRecord
from app.services.loan_payment_service import (
    compute_contractual_pi,
    get_payment_history,
    prepare_payments_for_engine,
)
from app.services.transfer_service import TransferSpec, create_transfer
from app.services import account_service


# ── Helpers ──────────────────────────────────────────────────────────


def _create_loan_account(seed_user):
    """Create a mortgage account with LoanParams for the test user.

    Returns:
        Account: the mortgage account.
    """
    loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Test Mortgage",
            anchor_balance=Decimal("200000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("250000.00"),
        current_principal=Decimal("200000.00"),
        term_months=360,
        origination_date=date(2024, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    # E-18 / Commit 15: origination LoanAnchorEvent.  This module
    # does not exercise the resolver directly (it tests
    # loan_payment_service which is a pure data-loading shim) but
    # downstream tests calling load_loan_context + resolver expect
    # an event-present invariant.  DH-#56: the rate now lives in the
    # origination RateHistory row (the retired LoanParams.interest_rate
    # column), so seed it alongside the anchor event.
    from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
        insert_origination_event,
        insert_origination_rate,
    )
    insert_origination_event(params)
    insert_origination_rate(params, Decimal("0.06500"))
    return account


def _create_transfer_to_loan(seed_user, loan_account, period, amount,
                              status_enum=StatusEnum.PROJECTED):
    """Create a transfer from checking to loan account.

    Uses the transfer service to ensure shadow transaction invariants
    are enforced (exactly two shadows, matching amounts/statuses).

    Args:
        seed_user: The seed_user fixture dict.
        loan_account: The destination loan account.
        period: The PayPeriod for the transfer.
        amount: Transfer amount as Decimal.
        status_enum: StatusEnum member for the initial status.

    Returns:
        Transfer: the created transfer.
    """
    return create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=ref_cache.status_id(status_enum),
            category_id=seed_user["categories"]["Rent"].id,
        ),
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestGetPaymentHistory:
    """Tests for get_payment_history() query and transformation logic."""

    def test_returns_empty_for_no_transfers(
        self, app, db, seed_user, seed_periods,
    ):
        """Account with no shadow transactions returns empty list."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert result == []

    def test_returns_income_shadows_only(
        self, app, db, seed_user, seed_periods,
    ):
        """Transfer creates expense + income shadows; only income returned.

        The expense shadow is on the checking (source) account and should
        not appear in the loan account's payment history.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            assert result[0].amount == Decimal("1500.00")

    def test_excludes_non_shadow_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Regular (non-transfer) income transactions are excluded.

        Only transactions with transfer_id IS NOT NULL are considered
        payment history.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)

            # Create a regular income transaction on the loan account
            # (not via transfer -- no transfer_id).
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=loan.id,
                status_id=projected_id,
                name="Manual Income",
                transaction_type_id=income_type_id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            # No transfer_id -> excluded.
            assert result == []

    def test_excludes_deleted_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleted shadow transactions are excluded."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            transfer = _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            # Soft-delete the income shadow.
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            shadow = (
                db.session.query(Transaction)
                .filter_by(
                    transfer_id=transfer.id,
                    transaction_type_id=income_type_id,
                    is_deleted=False,
                )
                .one()
            )
            shadow.is_deleted = True
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert result == []

    def test_excludes_cancelled_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelled-status shadow transactions are excluded.

        A cancelled transfer means the payment did not happen.  Including
        it would tell the engine a payment was made when it was not.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            transfer = _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.CANCELLED,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert result == []

    def test_uses_effective_amount_with_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Shadow with actual_amount populated: PaymentRecord uses actual.

        The effective_amount property prefers actual_amount when
        populated (per the 5A.1 fix).
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            transfer = _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()

            # Set actual_amount on the income shadow to a different value.
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            shadow = (
                db.session.query(Transaction)
                .filter_by(
                    transfer_id=transfer.id,
                    transaction_type_id=income_type_id,
                    is_deleted=False,
                )
                .one()
            )
            shadow.actual_amount = Decimal("1450.00")
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            # effective_amount returns actual when populated.
            assert result[0].amount == Decimal("1450.00")

    def test_uses_effective_amount_without_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Shadow without actual_amount: PaymentRecord uses estimated.

        When actual_amount is None, effective_amount falls back to
        estimated_amount.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            assert result[0].amount == Decimal("1500.00")

    def test_is_confirmed_settled_statuses(
        self, app, db, seed_user, seed_periods,
    ):
        """Paid/Settled shadow transactions produce is_confirmed=True.

        Paid has is_settled=True on the Status model.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,  # "Paid" -- is_settled=True
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            assert result[0].is_confirmed is True

    def test_is_confirmed_projected_status(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected shadow transactions produce is_confirmed=False.

        Projected has is_settled=False on the Status model.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            assert result[0].is_confirmed is False

    def test_payment_date_from_pay_period(
        self, app, db, seed_user, seed_periods,
    ):
        """PaymentRecord.payment_date matches txn.pay_period.start_date."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            assert result[0].payment_date == seed_periods[2].start_date

    def test_ordered_by_pay_period_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Results returned in chronological order by pay period date."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            # Create transfers in reverse period order.
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[3], Decimal("1500.00"),
            )
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1200.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 2
            assert result[0].payment_date < result[1].payment_date
            # First payment (earlier period) has the $1,200 amount.
            assert result[0].amount == Decimal("1200.00")
            assert result[1].amount == Decimal("1500.00")

    def test_filters_by_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """Transactions from a different scenario are excluded."""
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            # Query with a non-existent scenario ID.
            other_scenario = Scenario(
                user_id=seed_user["user"].id,
                name="What-If",
                is_baseline=False,
            )
            db.session.add(other_scenario)
            db.session.commit()

            result = get_payment_history(
                loan.id, other_scenario.id,
            )
            assert result == []

    def test_returns_decimal_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """PaymentRecord.amount is Decimal, not float."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 1
            assert isinstance(result[0].amount, Decimal)

    def test_multiple_payments_returned(
        self, app, db, seed_user, seed_periods,
    ):
        """Multiple transfers produce multiple PaymentRecords."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
            )
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[3], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, seed_user["scenario"].id,
            )
            assert len(result) == 3


# ── Tests for compute_contractual_pi ─────────────────────────────


class TestComputeContractualPi:
    """Tests for the contractual P&I calculation."""

    def test_fixed_rate_uses_original_terms(self, app, db, seed_user):
        """C1-1: Fixed-rate loan uses original principal and full term.

        $240,000 at 6.5% for 360 months.
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        r = 0.065/12; n = 360
        The engine's Decimal arithmetic produces $1,516.96.

        DH-#56: the rate is sourced from the rate-change feed (the
        origination RateChangeRecord), not the retired
        ``LoanParams.interest_rate`` column.
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        with app.app_context():
            params = LoanParams(
                account_id=1,
                original_principal=Decimal("240000.00"),
                current_principal=Decimal("237000.00"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
                is_arm=False,
            )

            result = compute_contractual_pi(
                params,
                rate_changes=[
                    RateChangeRecord(
                        effective_date=params.origination_date,
                        interest_rate=Decimal("0.06500"),
                        monthly_pi=None,
                    ),
                ],
            )

            # Standard amortization payment for $240k at 6.5% / 30yr.
            # Uses original_principal (not current) and full term.
            assert result == Decimal("1516.96")

    def test_arm_rate_from_origination_feed_uses_original_terms(
        self, app, db, seed_user,
    ):
        """C1-2: ARM loan whose only rate is the origination row uses original terms.

        DH-#56 retired the legacy pure-LoanParams fallback (it read the
        dropped ``LoanParams.interest_rate`` column).  The rate now comes
        from the rate-change feed; production callers go through
        :func:`load_loan_context`, which loads anchor_events and routes
        through :func:`loan_resolver.compute_monthly_payment_baseline`
        for the ARM-aware SSOT value (see
        :class:`TestComputeContractualPiArmAware`).  This pins the same
        value via the feed: an ARM whose rate-change feed carries only
        the origination 7.0% row (no recorded adjustment) holds the
        original-terms level payment in its first period.

            P = 250000.00, r = 0.07/12, n = 360
            M = P * [r(1+r)^n] / [(1+r)^n - 1] approx $1,663.26
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        with app.app_context():
            params = LoanParams(
                account_id=1,
                original_principal=Decimal("250000.00"),
                current_principal=Decimal("230000.00"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                is_arm=True,
            )

            result = compute_contractual_pi(
                params,
                rate_changes=[
                    RateChangeRecord(
                        effective_date=params.origination_date,
                        interest_rate=Decimal("0.07000"),
                        monthly_pi=None,
                    ),
                ],
            )

            # Origination-feed only: the first rate period holds the
            # original-terms amortization at the 7.0% origination rate.
            # Hand-computed: 250000 * 0.07/12 * (1.005833)^360 /
            # ((1.005833)^360 - 1) ~= 1663.26.
            assert result == Decimal("1663.26")


class TestComputeContractualPiArmAware:
    """C1-3..C1-5: ARM-aware behavior of compute_contractual_pi.

    Exercises the production path where :func:`load_loan_context`
    passes anchor_events + rate_changes + as_of through to
    :func:`loan_resolver.compute_monthly_payment_baseline`.  Locks
    the SSOT invariant: the returned value matches
    ``LoanState.monthly_payment`` for the same inputs, so the escrow-
    subtraction threshold in :func:`prepare_payments_for_engine`
    cannot under-subtract escrow for an ARM whose rate has adjusted
    since origination (the user-reported symptom -- schedule shows
    a 'Payment' value $33 above the loan card's total because the
    threshold leaked the original-terms P&I).
    """

    class _FakeAnchor:
        """Duck-typed LoanAnchorEvent for the resolver baseline."""

        def __init__(self, anchor_balance, anchor_date, created_at):
            self.anchor_balance = anchor_balance
            self.anchor_date = anchor_date
            self.created_at = created_at

    def test_arm_post_adjustment_holds_level_period_payment(
        self,
    ):
        """C1-3 (re-pinned): post-adjustment ARM holds the level period payment.

        Re-pinned under the rate-period model (CLAUDE rule 5 exception;
        the developer chose to hold the ARM payment constant within each
        fixed-rate period).  The prior test pinned $1,295.19 -- the
        payment from re-amortizing the reduced anchor balance
        ($177,999.54) over the remaining term.  A lender does NOT recast
        the payment unless the rate actually adjusts, so that was the
        symptom-#4 error; the anchor balance no longer influences the
        payment.

        Here the recorded rate never changes (a single 6.875% entry at
        origination), so by the amortization identity the period recast
        reproduces the original level payment: amortize($202,000,
        6.875%, 360) ~= $1,327, which the from-origination period walk
        reproduces to $1,326.99 (a cent of walk rounding).
        """
        from app.services.amortization_engine import RateChangeRecord
        params = LoanParams(
            account_id=1,
            original_principal=Decimal("202000.00"),
            current_principal=Decimal("177999.54"),
            term_months=360,
            origination_date=date(2018, 12, 1),
            payment_day=1,
            is_arm=True,
            arm_first_adjustment_months=60,  # 5/1 ARM, window ended 2023-12.
        )
        anchor = self._FakeAnchor(
            anchor_balance=Decimal("177999.54"),
            anchor_date=date(2026, 2, 15),
            created_at=date(2026, 2, 15),
        )
        # ARM rate at 6.875% since origination (no recorded adjustment).
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2018, 12, 1),
                interest_rate=Decimal("0.06875"),
            ),
        ]
        result = compute_contractual_pi(
            params,
            anchor_events=[anchor],
            rate_changes=rate_changes,
            as_of=date(2026, 5, 21),
        )
        # The level period payment -- with the recorded rate unchanged it
        # equals the original-terms payment within the walk's rounding,
        # NOT the old $1,295.19 re-amortization of the reduced balance.
        assert result == Decimal("1326.99")

    def test_fixed_rate_with_anchor_still_returns_original_terms(self):
        """C1-4: fixed-rate loans return original-terms regardless of anchor.

        Pre-payments accelerate the payoff date on a fixed-rate
        loan; the contractual P&I stays at the original amount.
        Routing through the resolver baseline preserves this --
        the fixed-rate branch in
        :func:`loan_resolver._compute_monthly_payment` ignores
        ``current_balance`` and uses ``original_principal``.

        DH-#56: the rate is sourced from the origination
        RateChangeRecord, not the retired ``LoanParams.interest_rate``
        column.
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        params = LoanParams(
            account_id=1,
            original_principal=Decimal("240000.00"),
            current_principal=Decimal("200000.00"),
            term_months=360,
            origination_date=date(2025, 1, 1),
            payment_day=1,
            is_arm=False,
        )
        anchor = self._FakeAnchor(
            anchor_balance=Decimal("200000.00"),
            anchor_date=date(2026, 5, 1),
            created_at=date(2026, 5, 1),
        )
        result = compute_contractual_pi(
            params,
            anchor_events=[anchor],
            rate_changes=[
                RateChangeRecord(
                    effective_date=params.origination_date,
                    interest_rate=Decimal("0.06500"),
                    monthly_pi=None,
                ),
            ],
            as_of=date(2026, 5, 21),
        )
        # Original-terms: $240k at 6.5% / 360 = $1,516.96.
        assert result == Decimal("1516.96")

    def test_empty_anchor_events_still_returns_original_terms(self):
        """C1-5: empty anchor_events does not affect the period P&I.

        Mirrors a hypothetical pre-anchor-backfill caller; production
        callers always pass a non-empty list (Commit 12's backfill
        invariant).  ``compute_contractual_pi`` does not read
        anchor_events (the period P&I is anchor-independent), so an
        empty list yields the same original-terms amortization.  The
        rate still comes from the rate-change feed (DH-#56 retired the
        ``LoanParams.interest_rate`` column); an empty feed is the only
        thing that raises.
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        params = LoanParams(
            account_id=1,
            original_principal=Decimal("240000.00"),
            current_principal=Decimal("200000.00"),
            term_months=360,
            origination_date=date(2025, 1, 1),
            payment_day=1,
            is_arm=False,
        )
        result = compute_contractual_pi(
            params,
            anchor_events=[],
            rate_changes=[
                RateChangeRecord(
                    effective_date=params.origination_date,
                    interest_rate=Decimal("0.06500"),
                    monthly_pi=None,
                ),
            ],
            as_of=date(2026, 5, 21),
        )
        # Original-terms amortization, independent of anchor data.
        assert result == Decimal("1516.96")


# ── Tests for prepare_payments_for_engine ────────────────────────


class TestPreparePaymentsForEngine:
    """Tests for escrow subtraction and biweekly redistribution."""

    def test_escrow_subtraction(self):
        """C1-3: Payments above P&I are reduced by escrow amount.

        Payment of $1,800, contractual P&I $1,517, escrow $283.
        The $283 above P&I is escrow -> subtract it -> $1,517.
        """
        payments = [
            PaymentRecord(date(2026, 1, 1), Decimal("1800.00"), True),
            PaymentRecord(date(2026, 2, 1), Decimal("1800.00"), True),
            PaymentRecord(date(2026, 3, 1), Decimal("1800.00"), True),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            monthly_escrow=Decimal("283.00"),
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 3
        for p in result:
            assert p.amount == Decimal("1517.00")

    def test_below_pi_not_adjusted(self):
        """C1-4: Payments at or below P&I are not reduced.

        Payment of $1,500 is below contractual P&I of $1,517 --
        this payment did not include escrow, so no subtraction.
        """
        payments = [
            PaymentRecord(date(2026, 1, 1), Decimal("1500.00"), True),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            monthly_escrow=Decimal("283.00"),
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 1
        assert result[0].amount == Decimal("1500.00")

    def test_biweekly_redistribution(self):
        """C1-5: Two payments due the same month are spread to consecutive months.

        With payment_day=1, pay periods starting 2026-01-02 and
        2026-01-16 both fall before 2026-02-01, so both have a true
        monthly DUE date of 2026-02-01 (the schedule keys rows by due
        date).  The first keeps its slot (due Feb 1); the second is
        redistributed to the next free due month, 2026-03-01.
        """
        payments = [
            PaymentRecord(date(2026, 1, 2), Decimal("1517.00"), True),
            PaymentRecord(date(2026, 1, 16), Decimal("1517.00"), True),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            monthly_escrow=Decimal("0.00"),
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 2
        # First keeps its original pay-period-start date (due Feb 1).
        assert result[0].payment_date == date(2026, 1, 2)
        # Second is redistributed to the next free due month (Mar 1).
        assert result[1].payment_date == date(2026, 3, 1)

    def test_empty_payments_passthrough(self):
        """Empty payment list returns unchanged."""
        result = prepare_payments_for_engine(
            [],
            payment_day=1,
            monthly_escrow=Decimal("283.00"),
            contractual_pi=Decimal("1517.00"),
        )
        assert result == []

    def test_no_escrow_no_subtraction(self):
        """Zero escrow means no subtraction regardless of amount."""
        payments = [
            PaymentRecord(date(2026, 1, 1), Decimal("2000.00"), True),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            monthly_escrow=Decimal("0.00"),
            contractual_pi=Decimal("1517.00"),
        )

        assert result[0].amount == Decimal("2000.00")

    def test_preserves_is_confirmed(self):
        """is_confirmed flag is preserved through preparation."""
        payments = [
            PaymentRecord(date(2026, 1, 1), Decimal("1800.00"), True),
            PaymentRecord(date(2026, 2, 1), Decimal("1800.00"), False),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            monthly_escrow=Decimal("283.00"),
            contractual_pi=Decimal("1517.00"),
        )

        assert result[0].is_confirmed is True
        assert result[1].is_confirmed is False

    def test_december_to_january_rollover(self):
        """Two payments both due Jan 1 2027 (year rollover): second to Feb 2027.

        With payment_day=1, pay periods starting 2026-12-05 and
        2026-12-19 both fall before 2027-01-01, so both have a true
        monthly due date of 2027-01-01 (the due date crosses the year
        boundary).  The second is redistributed to the next free due
        month, 2027-02-01.
        """
        payments = [
            PaymentRecord(date(2026, 12, 5), Decimal("1517.00"), True),
            PaymentRecord(date(2026, 12, 19), Decimal("1517.00"), True),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            monthly_escrow=Decimal("0.00"),
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 2
        assert result[0].payment_date == date(2026, 12, 5)
        assert result[1].payment_date == date(2027, 2, 1)
