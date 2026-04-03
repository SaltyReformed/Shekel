"""
Shekel Budget App -- Savings Dashboard Service Tests

Unit tests for the savings_dashboard_service module, verifying that
the extracted business logic produces correct financial computations
independently of the Flask route layer.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.services import savings_dashboard_service, pay_period_service


class TestComputeDashboardData:
    """Tests for the top-level compute_dashboard_data orchestrator."""

    def test_returns_expected_keys(self, app, db, seed_user, seed_periods):
        """Return dict contains all template context keys."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            expected_keys = {
                "account_data", "grouped_accounts", "goal_data",
                "emergency_metrics", "total_savings",
                "avg_monthly_expenses", "savings_accounts",
            }
            assert set(result.keys()) == expected_keys

    def test_empty_user_returns_safe_defaults(self, app, db, seed_user):
        """User with no periods or goals gets safe zero-value defaults.

        The seed user has a Checking account ($1000) which is liquid,
        so total_savings reflects that even without pay periods.
        """
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert result["total_savings"] == Decimal("1000.00")
            assert result["avg_monthly_expenses"] == Decimal("0.00")
            assert result["goal_data"] == []

    def test_checking_account_appears_in_account_data(
        self, app, db, seed_user, seed_periods
    ):
        """The seed user's checking account appears in account_data."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            acct_names = [
                ad["account"].name for ad in result["account_data"]
            ]
            assert "Checking" in acct_names

    def test_account_has_current_balance(
        self, app, db, seed_user, seed_periods
    ):
        """Each account_data entry has a current_balance key."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            for ad in result["account_data"]:
                assert "current_balance" in ad
                assert isinstance(
                    ad["current_balance"], (Decimal, type(None))
                )


class TestGroupAccountsByCategory:
    """Tests for the category grouping logic."""

    def test_checking_grouped_as_asset(
        self, app, db, seed_user, seed_periods
    ):
        """Checking accounts are grouped under the 'asset' category."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            grouped = result["grouped_accounts"]
            assert "asset" in grouped
            asset_names = [
                ad["account"].name for ad in grouped["asset"]
            ]
            assert "Checking" in asset_names

    def test_savings_account_grouped_as_asset(
        self, app, db, seed_user, seed_periods
    ):
        """Savings accounts are grouped under 'asset'."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Emergency Fund",
                current_anchor_balance=Decimal("10000.00"),
            )
            db.session.add(savings)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            grouped = result["grouped_accounts"]
            asset_names = [
                ad["account"].name for ad in grouped.get("asset", [])
            ]
            assert "Emergency Fund" in asset_names


class TestGoalProgress:
    """Tests for savings goal progress computation."""

    def test_goal_progress_with_target(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with balance at 50% of target shows 50% progress."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Goal Account",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Vacation",
                target_amount=Decimal("10000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert len(result["goal_data"]) == 1
            gd = result["goal_data"][0]
            assert gd["progress_pct"] == 50
            assert gd["current_balance"] == Decimal("5000.00")

    def test_no_goals_returns_empty_list(
        self, app, db, seed_user, seed_periods
    ):
        """User with no active goals gets an empty goal_data list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert result["goal_data"] == []


class TestEmergencyFundMetrics:
    """Tests for emergency fund coverage computation."""

    def test_total_savings_sums_savings_accounts(
        self, app, db, seed_user, seed_periods
    ):
        """total_savings includes savings + HYSA balances only."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("8000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            # Both Checking ($1000, liquid) and Savings ($8000, liquid)
            # contribute to total_savings.
            assert result["total_savings"] == Decimal("9000.00")


# ── Paid-Off Flag Tests (Commit 5.9-2) ──────────────────────────────


def _create_small_loan(seed_user, db_session, name="Test Loan",
                       principal=Decimal("1000.00"),
                       rate=Decimal("0.05000"), term=24):
    """Create a small loan account with LoanParams for paid-off testing.

    Uses a small principal for fast engine replay and easy verification.
    Origination is Jan 2026 with term=24 so remaining months is
    comfortably positive (~21 from April 2026).
    """
    loan_type = db_session.query(AccountType).filter_by(name="Auto Loan").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
        current_anchor_balance=principal,
    )
    db_session.add(account)
    db_session.flush()

    from app.models.loan_params import LoanParams as LP  # pylint: disable=import-outside-toplevel
    params = LP(
        account_id=account.id,
        original_principal=principal,
        current_principal=principal,
        interest_rate=rate,
        term_months=term,
        origination_date=date(2026, 1, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.commit()
    return account


class TestPaidOffFlag:
    """Tests for the is_paid_off flag in account data.

    Commit 5.9-2: the savings dashboard service determines whether a
    loan is paid off by replaying only confirmed (Paid/Settled) payments
    through the amortization engine.  Projected payments are excluded.
    """

    def test_paid_off_true_when_confirmed_covers_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Confirmed payment covering the full balance sets is_paid_off=True.

        A $1,000 loan at 5% for 12 months.  A single confirmed payment
        of $1,100 exceeds principal + first-month interest (~$1,004.17).
        The engine's overpayment guard caps the payment at the remaining
        balance + interest, resulting in remaining_balance = $0.00.
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is True

    def test_paid_off_false_no_confirmed_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Loan with no payments at all: is_paid_off=False."""
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False

    def test_paid_off_false_partial_confirmed_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Partial confirmed payment leaving balance > 0: is_paid_off=False.

        A $500 payment on a $1,000 loan leaves ~$504 (principal minus
        payment plus interest).
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("500.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False

    def test_paid_off_false_projected_only(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected payment that would pay off the loan: is_paid_off=False.

        The critical semantic test -- projections do not equal payoff.
        A projected transfer of $1,100 covers the full balance, but
        since it has Projected status (is_settled=False), the paid-off
        flag must remain False.
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False

    def test_paid_off_false_for_non_loan_account(
        self, app, db, seed_user, seed_periods,
    ):
        """Non-loan accounts (checking, savings) have is_paid_off=False."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            # The seed user's checking account is non-amortizing.
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].name == "Checking"
            )
            assert checking_ad["is_paid_off"] is False

    def test_paid_off_false_no_loan_params(
        self, app, db, seed_user, seed_periods,
    ):
        """Loan account with no LoanParams: is_paid_off=False, no crash."""
        with app.app_context():
            loan_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
            )
            db.session.add(acct)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False
