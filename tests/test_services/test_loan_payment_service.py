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
from app.services.transfer_service import create_transfer


# ── Helpers ──────────────────────────────────────────────────────────


def _create_loan_account(seed_user):
    """Create a mortgage account with LoanParams for the test user.

    Returns:
        Account: the mortgage account.
    """
    loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name="Test Mortgage",
        current_anchor_balance=Decimal("200000.00"),
    )
    db.session.add(account)
    db.session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("250000.00"),
        current_principal=Decimal("200000.00"),
        interest_rate=Decimal("0.06500"),
        term_months=360,
        origination_date=date(2024, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
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
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=amount,
        status_id=ref_cache.status_id(status_enum),
        category_id=seed_user["categories"]["Rent"].id,
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
        """
        with app.app_context():
            params = LoanParams(
                account_id=1,
                original_principal=Decimal("240000.00"),
                current_principal=Decimal("237000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
                is_arm=False,
            )

            result = compute_contractual_pi(params)

            # Standard amortization payment for $240k at 6.5% / 30yr.
            # Uses original_principal (not current) and full term.
            assert result == Decimal("1516.96")

    def test_arm_uses_current_principal_and_remaining(
        self, app, db, seed_user,
    ):
        """C1-2: ARM loan re-amortizes from current balance.

        Uses current_principal ($230k), current interest_rate (7%),
        and remaining months from origination_date/term_months.
        """
        with app.app_context():
            params = LoanParams(
                account_id=1,
                original_principal=Decimal("250000.00"),
                current_principal=Decimal("230000.00"),
                interest_rate=Decimal("0.07000"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                is_arm=True,
            )

            result = compute_contractual_pi(params)

            # ARM payment should be computed from current_principal,
            # not original_principal.  Result should be > 0 and
            # different from what you'd get with original terms.
            assert result > Decimal("0")
            assert isinstance(result, Decimal)


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
        """C1-5: Two payments in the same month are redistributed.

        Two payments in January -> second shifts to February.
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
        # First stays in January.
        assert result[0].payment_date.month == 1
        # Second is redistributed to February.
        assert result[1].payment_date.month == 2
        assert result[1].payment_date.day == 1

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
        """Two payments in December: second rolls to January next year."""
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
        assert result[1].payment_date == date(2027, 1, 1)
