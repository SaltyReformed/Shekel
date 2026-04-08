"""
Shekel Budget App -- Dashboard Service Tests

Tests for the dashboard aggregation service: upcoming bills, alerts,
balance/cash runway, payday info, savings goals, debt summary,
spending comparison, and full integration.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.services import dashboard_service


# ── Helpers ──────────────────────────────────────────────────────────


def _add_txn(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    actual_amount=None,
):
    """Create a transaction for testing.

    Returns the created Transaction.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(actual_amount)) if actual_amount is not None else None,
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_anchor_history(db_session, account, period, balance, days_ago=0):
    """Create an anchor history entry.

    Args:
        days_ago: How many days in the past the entry's created_at should be.

    Returns:
        The created AccountAnchorHistory.
    """
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=period.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


# ── Empty/Minimal State Tests ───────────────────────────────────────


class TestDashboardEmpty:
    """Tests for dashboard with minimal/no data."""

    def test_dashboard_empty_user(self, app, seed_user):
        """All sections return empty/zero/None gracefully with no data."""
        with app.app_context():
            result = dashboard_service.compute_dashboard_data(
                user_id=seed_user["user"].id,
            )
            # Should not crash; basic structure present.
            assert result["has_default_account"] is True
            assert result["upcoming_bills"] == []
            assert isinstance(result["alerts"], list)

    def test_dashboard_no_periods(self, app, seed_user):
        """User with account but no periods -> graceful degradation."""
        with app.app_context():
            result = dashboard_service.compute_dashboard_data(
                user_id=seed_user["user"].id,
            )
            assert result["upcoming_bills"] == []
            assert result["payday_info"]["days_until"] is None
            assert result["spending_comparison"]["direction"] is None


# ── Upcoming Bills Tests ────────────────────────────────────────────


class TestUpcomingBills:
    """Tests for the upcoming bills section."""

    def test_upcoming_bills_filters_paid(self, app, seed_user, seed_periods, db):
        """Paid transactions excluded; only projected bills returned."""
        with app.app_context():
            p = seed_periods[0]
            _add_txn(db.session, seed_user, p, "Unpaid 1", "500.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, p, "Unpaid 2", "300.00",
                     due_date=date(2026, 1, 8))
            _add_txn(db.session, seed_user, p, "Paid", "200.00",
                     status_enum=StatusEnum.DONE,
                     due_date=date(2026, 1, 3))
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            assert len(bills) == 2
            names = [b["name"] for b in bills]
            assert "Paid" not in names

    def test_upcoming_bills_two_periods(self, app, seed_user, seed_periods, db):
        """Bills from both current and next period included."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Bill P0", "100.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[1], "Bill P1", "200.00",
                     due_date=date(2026, 1, 20))
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            assert len(bills) == 2

    def test_upcoming_bills_sorted_by_due_date(self, app, seed_user, seed_periods, db):
        """Bills sorted by due_date ascending."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Late", "100.00",
                     due_date=date(2026, 1, 15))
            _add_txn(db.session, seed_user, seed_periods[0], "Early", "100.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[0], "Mid", "100.00",
                     due_date=date(2026, 1, 10))
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            names = [b["name"] for b in bills]
            assert names == ["Early", "Mid", "Late"]

    def test_upcoming_bills_excludes_income(self, app, seed_user, seed_periods, db):
        """Income transactions not included in bills."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Expense", "500.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[0], "Paycheck", "2000.00",
                     is_income=True, due_date=date(2026, 1, 2))
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            assert len(bills) == 1
            assert bills[0]["name"] == "Expense"

    def test_upcoming_bills_excludes_deleted(self, app, seed_user, seed_periods, db):
        """Soft-deleted transactions excluded from bills."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "Active", "100.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, seed_periods[0], "Deleted", "200.00",
                     due_date=date(2026, 1, 6), is_deleted=True)
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            assert len(bills) == 1
            assert bills[0]["name"] == "Active"

    def test_upcoming_bills_null_due_date_sorted(self, app, seed_user, seed_periods, db):
        """Bills without due_date sort by period start_date."""
        with app.app_context():
            _add_txn(db.session, seed_user, seed_periods[0], "NoDue", "100.00",
                     due_date=None)
            _add_txn(db.session, seed_user, seed_periods[0], "HasDue", "100.00",
                     due_date=date(2026, 1, 3))
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            # NoDue uses period.start_date (Jan 2) as sort key,
            # HasDue uses due_date (Jan 3).
            assert bills[0]["name"] == "NoDue"
            assert bills[1]["name"] == "HasDue"

    def test_upcoming_bills_includes_days_until_due(self, app, seed_user, seed_periods, db):
        """Bills include days_until_due based on due_date."""
        with app.app_context():
            future_date = date.today() + timedelta(days=5)
            _add_txn(db.session, seed_user, seed_periods[0], "Future", "100.00",
                     due_date=future_date)
            db.session.commit()

            bills = dashboard_service._get_upcoming_bills(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                seed_periods[1],
            )
            assert len(bills) == 1
            assert bills[0]["days_until_due"] == 5


# ── Alert Tests ─────────────────────────────────────────────────────


class TestAlerts:
    """Tests for the alerts section."""

    def test_alert_stale_anchor(self, app, seed_user, seed_periods, db):
        """Stale anchor alert when last update > staleness threshold."""
        with app.app_context():
            account = seed_user["account"]
            _add_anchor_history(
                db.session, account, seed_periods[0], "1000.00", days_ago=20,
            )
            db.session.commit()

            settings = seed_user["settings"]
            # staleness_days defaults to 14.
            alerts = dashboard_service._compute_alerts(
                account, settings, {}, seed_periods[0], seed_periods,
            )
            stale = [a for a in alerts if a["type"] == "stale_anchor"]
            assert len(stale) == 1
            assert stale[0]["severity"] == "warning"
            # days_ago=20 may show 19 or 20 depending on time-of-day.
            assert "days" in stale[0]["message"]

    def test_alert_no_stale_anchor(self, app, seed_user, seed_periods, db):
        """No stale anchor alert when recently updated."""
        with app.app_context():
            account = seed_user["account"]
            _add_anchor_history(
                db.session, account, seed_periods[0], "1000.00", days_ago=5,
            )
            db.session.commit()

            alerts = dashboard_service._compute_alerts(
                account, seed_user["settings"], {}, seed_periods[0], seed_periods,
            )
            stale = [a for a in alerts if a["type"] == "stale_anchor"]
            assert len(stale) == 0

    def test_alert_negative_balance(self, app, seed_user, seed_periods, db):
        """Negative balance alert for first future period with balance < 0."""
        with app.app_context():
            # Simulate balance results with a future negative period.
            balance_results = {}
            for p in seed_periods:
                balance_results[p.id] = Decimal("100.00")
            # Make a future period negative.
            future_period = None
            for p in seed_periods:
                if p.start_date > date.today():
                    future_period = p
                    break
            if future_period:
                balance_results[future_period.id] = Decimal("-500.00")

                # Need a recent anchor to avoid stale alert noise.
                _add_anchor_history(
                    db.session, seed_user["account"],
                    seed_periods[0], "1000.00", days_ago=1,
                )
                db.session.commit()

                alerts = dashboard_service._compute_alerts(
                    seed_user["account"], seed_user["settings"],
                    balance_results, seed_periods[0], seed_periods,
                )
                neg = [a for a in alerts if a["type"] == "negative_balance"]
                assert len(neg) == 1
                assert neg[0]["severity"] == "danger"

    def test_alert_no_anchor_history(self, app, seed_user, seed_periods):
        """No anchor history -> stale anchor alert."""
        with app.app_context():
            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                {}, seed_periods[0], seed_periods,
            )
            stale = [a for a in alerts if a["type"] == "stale_anchor"]
            assert len(stale) == 1

    def test_alerts_sorted_by_severity(self, app, seed_user, seed_periods, db):
        """Danger alerts come before warning alerts."""
        with app.app_context():
            # Create both stale anchor (warning) and negative balance (danger).
            balance_results = {}
            future = None
            for p in seed_periods:
                balance_results[p.id] = Decimal("100.00")
                if p.start_date > date.today() and future is None:
                    future = p
            if future:
                balance_results[future.id] = Decimal("-100.00")
            # No anchor history -> stale anchor warning.
            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                balance_results, seed_periods[0], seed_periods,
            )
            if len(alerts) >= 2:
                severities = [a["severity"] for a in alerts]
                danger_idx = next(
                    (i for i, s in enumerate(severities) if s == "danger"), None,
                )
                warning_idx = next(
                    (i for i, s in enumerate(severities) if s == "warning"), None,
                )
                if danger_idx is not None and warning_idx is not None:
                    assert danger_idx < warning_idx

    def test_no_alerts_clean_state(self, app, seed_user, seed_periods, db):
        """No alerts when everything is current and positive."""
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                seed_periods[0], "5000.00", days_ago=1,
            )
            db.session.commit()

            balance_results = {p.id: Decimal("5000.00") for p in seed_periods}
            alerts = dashboard_service._compute_alerts(
                seed_user["account"], seed_user["settings"],
                balance_results, seed_periods[0], seed_periods,
            )
            # Should have no stale, no negative, no low balance.
            assert len(alerts) == 0


# ── Balance and Cash Runway Tests ───────────────────────────────────


class TestBalanceInfo:
    """Tests for balance and cash runway section."""

    def test_cash_runway_zero_spending(self, app, seed_user, seed_periods):
        """Zero spending -> runway_days=None (not infinity)."""
        with app.app_context():
            balance = {seed_periods[0].id: Decimal("3000.00")}
            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_periods[0], balance,
            )
            assert result["cash_runway_days"] is None

    def test_cash_runway_negative_balance(self, app, seed_user, seed_periods):
        """Negative balance -> runway_days=0."""
        with app.app_context():
            balance = {seed_periods[0].id: Decimal("-500.00")}
            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_periods[0], balance,
            )
            assert result["cash_runway_days"] == 0

    def test_balance_from_calculator(self, app, seed_user, seed_periods, db):
        """Current balance comes from balance calculator results."""
        with app.app_context():
            expected = Decimal("2500.00")
            balance = {seed_periods[0].id: expected}
            result = dashboard_service._get_balance_info(
                seed_user["account"], seed_periods[0], balance,
            )
            assert result["current_balance"] == expected


# ── Payday Info Tests ───────────────────────────────────────────────


class TestPaydayInfo:
    """Tests for the payday info section."""

    def test_payday_info_future_period(self, app, seed_user, seed_periods):
        """Next period with start_date > today -> days_until populated."""
        with app.app_context():
            result = dashboard_service._get_payday_info(
                seed_user["user"].id, seed_periods,
            )
            # At least some periods should be in the future (test data is 2026).
            if result["next_date"] is not None:
                assert result["days_until"] >= 0

    def test_payday_info_no_salary(self, app, seed_user, seed_periods):
        """No salary profile -> next_amount=None, days_until still populated."""
        with app.app_context():
            result = dashboard_service._get_payday_info(
                seed_user["user"].id, seed_periods,
            )
            # seed_user has no salary profile.
            assert result["next_amount"] is None
            # But next_date should be populated (periods are in 2026).
            assert result["next_date"] is not None

    def test_payday_info_no_future_period(self, app, seed_user, db):
        """No future periods -> all fields None."""
        with app.app_context():
            # Create periods in the past.
            from app.services import pay_period_service
            old_periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2020, 1, 2),
                num_periods=5,
                cadence_days=14,
            )
            db.session.commit()

            result = dashboard_service._get_payday_info(
                seed_user["user"].id, old_periods,
            )
            assert result["days_until"] is None
            assert result["next_amount"] is None
            assert result["next_date"] is None


# ── Savings Goals Tests ─────────────────────────────────────────────


class TestSavingsGoals:
    """Tests for savings goals progress."""

    def test_savings_goals_progress(self, app, seed_user, seed_periods, db):
        """Goal progress computed correctly from account balance and target."""
        with app.app_context():
            # Create a dedicated savings account with known balance.
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings_acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Goal Account",
                current_anchor_balance=Decimal("2500.00"),
            )
            db.session.add(savings_acct)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings_acct.id,
                name="Emergency Fund",
                target_amount=Decimal("10000.00"),
            )
            db.session.add(goal)
            db.session.commit()

            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            assert len(goals) == 1
            # 2500 / 10000 * 100 = 25.00%
            assert goals[0]["pct_complete"] == Decimal("25.00")

    def test_savings_goals_empty(self, app, seed_user):
        """No active goals -> empty list."""
        with app.app_context():
            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            assert goals == []

    def test_savings_goals_clamped_100(self, app, seed_user, db):
        """Balance exceeding target -> pct_complete clamped to 100."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            rich_acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Over Goal",
                current_anchor_balance=Decimal("15000.00"),
            )
            db.session.add(rich_acct)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=rich_acct.id,
                name="Reached Goal",
                target_amount=Decimal("10000.00"),
            )
            db.session.add(goal)
            db.session.commit()

            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            assert goals[0]["pct_complete"] == Decimal("100.00")

    def test_savings_goals_null_target(self, app, seed_user, db):
        """Null target_amount (income-relative goal) -> pct_complete=0, no crash."""
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Income Goal",
                target_amount=None,
            )
            db.session.add(goal)
            db.session.commit()

            goals = dashboard_service._get_savings_goals(seed_user["user"].id)
            # Null target treated as zero -> pct_complete=0.
            assert goals[0]["pct_complete"] == Decimal("0")


# ── Debt Summary Tests ──────────────────────────────────────────────


class TestDebtSummary:
    """Tests for the debt summary section."""

    def test_debt_summary_no_debt(self, app, seed_user, seed_periods):
        """No debt accounts -> debt_summary is None."""
        with app.app_context():
            result = dashboard_service._get_debt_summary(seed_user["user"].id)
            assert result is None


# ── Spending Comparison Tests ───────────────────────────────────────


class TestSpendingComparison:
    """Tests for the spending comparison section."""

    def test_spending_comparison_higher(self, app, seed_user, seed_periods, db):
        """Current > prior -> delta positive, direction='higher'."""
        with app.app_context():
            p0 = seed_periods[0]
            p1 = seed_periods[1]
            _add_txn(db.session, seed_user, p0, "Prior", "600.00",
                     status_enum=StatusEnum.DONE, actual_amount="600.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, p1, "Current", "800.00",
                     status_enum=StatusEnum.DONE, actual_amount="800.00",
                     due_date=date(2026, 1, 20))
            db.session.commit()

            result = dashboard_service._get_spending_comparison(
                seed_user["account"].id,
                seed_user["scenario"].id,
                p1,
                seed_periods,
            )
            assert result["current_total"] == Decimal("800.00")
            assert result["prior_total"] == Decimal("600.00")
            assert result["delta"] == Decimal("200.00")
            assert result["direction"] == "higher"

    def test_spending_comparison_lower(self, app, seed_user, seed_periods, db):
        """Current < prior -> delta negative, direction='lower'."""
        with app.app_context():
            p0 = seed_periods[0]
            p1 = seed_periods[1]
            _add_txn(db.session, seed_user, p0, "Prior", "600.00",
                     status_enum=StatusEnum.DONE, actual_amount="600.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, p1, "Current", "400.00",
                     status_enum=StatusEnum.DONE, actual_amount="400.00",
                     due_date=date(2026, 1, 20))
            db.session.commit()

            result = dashboard_service._get_spending_comparison(
                seed_user["account"].id,
                seed_user["scenario"].id,
                p1,
                seed_periods,
            )
            assert result["delta"] == Decimal("-200.00")
            assert result["direction"] == "lower"

    def test_spending_comparison_same(self, app, seed_user, seed_periods, db):
        """Same spending -> delta=0, direction='same'."""
        with app.app_context():
            p0 = seed_periods[0]
            p1 = seed_periods[1]
            _add_txn(db.session, seed_user, p0, "Prior", "500.00",
                     status_enum=StatusEnum.DONE, actual_amount="500.00",
                     due_date=date(2026, 1, 5))
            _add_txn(db.session, seed_user, p1, "Current", "500.00",
                     status_enum=StatusEnum.DONE, actual_amount="500.00",
                     due_date=date(2026, 1, 20))
            db.session.commit()

            result = dashboard_service._get_spending_comparison(
                seed_user["account"].id,
                seed_user["scenario"].id,
                p1,
                seed_periods,
            )
            assert result["delta"] == Decimal("0")
            assert result["direction"] == "same"

    def test_spending_comparison_no_prior(self, app, seed_user, seed_periods, db):
        """First period -> prior_total=None, delta=None."""
        with app.app_context():
            result = dashboard_service._get_spending_comparison(
                seed_user["account"].id,
                seed_user["scenario"].id,
                seed_periods[0],
                [seed_periods[0]],  # Only one period, no prior.
            )
            assert result["prior_total"] is None
            assert result["delta"] is None
            assert result["direction"] is None

    def test_spending_comparison_zero_prior(self, app, seed_user, seed_periods, db):
        """Zero prior spending -> delta_pct=None (div by zero guard)."""
        with app.app_context():
            p1 = seed_periods[1]
            _add_txn(db.session, seed_user, p1, "Current", "500.00",
                     status_enum=StatusEnum.DONE, actual_amount="500.00",
                     due_date=date(2026, 1, 20))
            db.session.commit()

            result = dashboard_service._get_spending_comparison(
                seed_user["account"].id,
                seed_user["scenario"].id,
                p1,
                seed_periods,
            )
            assert result["prior_total"] == Decimal("0")
            assert result["delta_pct"] is None

    def test_spending_comparison_only_paid(self, app, seed_user, seed_periods, db):
        """Only settled expenses counted; projected excluded."""
        with app.app_context():
            p1 = seed_periods[1]
            _add_txn(db.session, seed_user, p1, "Paid", "500.00",
                     status_enum=StatusEnum.DONE, actual_amount="500.00",
                     due_date=date(2026, 1, 20))
            _add_txn(db.session, seed_user, p1, "Projected", "999.00",
                     status_enum=StatusEnum.PROJECTED,
                     due_date=date(2026, 1, 21))
            db.session.commit()

            result = dashboard_service._get_spending_comparison(
                seed_user["account"].id,
                seed_user["scenario"].id,
                p1,
                seed_periods,
            )
            assert result["current_total"] == Decimal("500.00")


# ── Integration Tests ───────────────────────────────────────────────


class TestFullDashboard:
    """Integration tests for the full dashboard."""

    def test_full_dashboard_integration(self, app, seed_full_user_data):
        """Full dashboard with rich data -> all sections populated, no errors."""
        with app.app_context():
            user_id = seed_full_user_data["user"].id
            result = dashboard_service.compute_dashboard_data(user_id)

            assert result["has_default_account"] is True
            assert isinstance(result["upcoming_bills"], list)
            assert isinstance(result["alerts"], list)
            assert isinstance(result["savings_goals"], list)
            assert isinstance(result["spending_comparison"], dict)
            assert "current_total" in result["spending_comparison"]
            assert result["payday_info"] is not None
