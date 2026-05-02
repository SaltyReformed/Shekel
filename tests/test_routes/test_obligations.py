"""
Tests for the recurring obligations summary route.

Covers page rendering, empty state, monthly equivalent calculations,
summary totals, IDOR isolation, filtering of inactive/non-recurring
templates, and section grouping correctness.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrencePattern, TransactionType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate


# ── Helpers ──────────────────────────────────────────────────────────


def _get_checking(user):
    """Return the seed_user's checking account."""
    return (
        db.session.query(Account)
        .filter_by(user_id=user.id, name="Test Checking")
        .first()
    )


def _create_savings_account(user, db_session, name="Test Savings"):
    """Create a savings account for the given user."""
    savings_type = db_session.query(AccountType).filter_by(name="Savings").one()
    account = Account(
        user_id=user.id,
        account_type_id=savings_type.id,
        name=name,
        current_anchor_balance=Decimal("5000.00"),
    )
    db_session.add(account)
    db_session.flush()
    return account


def _create_rule(user, db_session, pattern_name, interval_n=1,
                 day_of_month=None, month_of_year=None):
    """Create a RecurrenceRule with the specified pattern.

    Args:
        user: User model instance.
        db_session: Active DB session.
        pattern_name: Exact name from ref.recurrence_patterns
            (e.g., "Every Period", "Monthly", "Annual").
        interval_n: For "Every N Periods" pattern.
        day_of_month: For "Monthly" / "Annual" patterns.
        month_of_year: For "Annual" pattern.

    Returns:
        RecurrenceRule instance (flushed, has ID).
    """
    pattern = db_session.query(RecurrencePattern).filter_by(name=pattern_name).one()
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=pattern.id,
        interval_n=interval_n,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
    )
    db_session.add(rule)
    db_session.flush()
    return rule


def _create_expense_template(user, db_session, account, category,
                             name, amount, rule):
    """Create a recurring expense TransactionTemplate."""
    expense_type = db_session.query(TransactionType).filter_by(name="Expense").one()
    tmpl = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=category.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db_session.add(tmpl)
    db_session.flush()
    return tmpl


def _create_income_template(user, db_session, account, category,
                            name, amount, rule):
    """Create a recurring income TransactionTemplate."""
    income_type = db_session.query(TransactionType).filter_by(name="Income").one()
    tmpl = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=category.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db_session.add(tmpl)
    db_session.flush()
    return tmpl


def _create_transfer_template(user, db_session, from_account, to_account,
                              name, amount, rule):
    """Create a recurring TransferTemplate."""
    tmpl = TransferTemplate(
        user_id=user.id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        recurrence_rule_id=rule.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db_session.add(tmpl)
    db_session.flush()
    return tmpl


# ── Page Rendering Tests ─────────────────────────────────────────────


class TestObligationsSummary:
    """Tests for the GET /obligations page."""

    def test_page_renders_with_mixed_templates(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with expense, income, and transfer templates returns 200
        and contains all three section headers and template names.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        savings = _create_savings_account(user, db.session)

        rule_bw = _create_rule(user, db.session, "Every Period")
        rule_mo = _create_rule(user, db.session, "Monthly", day_of_month=15)

        _create_expense_template(
            user, db.session, checking, category,
            "Electricity", "100.00", rule_bw,
        )
        _create_income_template(
            user, db.session, checking, category,
            "Paycheck", "1500.00", rule_bw,
        )
        _create_transfer_template(
            user, db.session, checking, savings,
            "Savings Transfer", "500.00", rule_mo,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "Recurring Expenses" in html
        assert "Recurring Transfers" in html
        assert "Recurring Income" in html
        assert "Electricity" in html
        assert "Paycheck" in html
        assert "Savings Transfer" in html

    def test_empty_state(self, auth_client, seed_user, db, seed_periods_today):
        """GET with no templates shows the empty-state message."""
        resp = auth_client.get("/obligations")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No Recurring Obligations" in html
        # The obligation tables should not be present (base.html may
        # have unrelated table elements like keyboard shortcuts).
        assert "Recurring Expenses" not in html

    def test_requires_login(self, client):
        """GET without authentication redirects to login."""
        resp = client.get("/obligations")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


# ── Monthly Equivalent Tests ─────────────────────────────────────────


class TestMonthlyEquivalents:
    """Tests for correct monthly equivalent calculations."""

    def test_biweekly_monthly_equivalent(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A biweekly $100 expense has monthly equivalent of $216.67.

        Calculation: 100 * 26 / 12 = 216.666... -> $216.67 (ROUND_HALF_UP)
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Every Period")

        _create_expense_template(
            user, db.session, checking, category,
            "Biweekly Bill", "100.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        assert resp.status_code == 200
        html = resp.data.decode()

        expected = (Decimal("100") * Decimal("26") / Decimal("12")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        assert f"${expected:,.2f}" in html

    def test_monthly_equivalent_identity(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A monthly $500 expense has monthly equivalent of $500.00."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Monthly", day_of_month=15)

        _create_expense_template(
            user, db.session, checking, category,
            "Monthly Bill", "500.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "$500.00" in html

    def test_annual_monthly_equivalent(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An annual $1,200 expense has monthly equivalent of $100.00."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(
            user, db.session, "Annual",
            day_of_month=1, month_of_year=6,
        )

        _create_expense_template(
            user, db.session, checking, category,
            "Annual Insurance", "1200.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "$100.00" in html

    def test_summary_totals_correct(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Summary totals correctly sum expenses, transfers, and income.

        Setup: $100 biweekly expense, $500 monthly transfer, $1500 biweekly income.
        Expected:
          expense_monthly = 100 * 26 / 12 = $216.67
          transfer_monthly = $500.00
          income_monthly = 1500 * 26 / 12 = $3,250.00
          total_outflows = 216.67 + 500.00 = $716.67
          net = 3250.00 - 716.67 = $2,533.33
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        savings = _create_savings_account(user, db.session)

        rule_bw = _create_rule(user, db.session, "Every Period")
        rule_mo = _create_rule(user, db.session, "Monthly", day_of_month=1)

        _create_expense_template(
            user, db.session, checking, category,
            "Expense", "100.00", rule_bw,
        )
        _create_transfer_template(
            user, db.session, checking, savings,
            "Transfer", "500.00", rule_mo,
        )
        _create_income_template(
            user, db.session, checking, category,
            "Paycheck", "1500.00", rule_bw,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()

        # Verify individual monthly equivalents.
        assert "$216.67" in html    # expense monthly
        assert "$500.00" in html    # transfer monthly
        assert "$3,250.00" in html  # income monthly

        # Verify outflows = expense + transfer.
        assert "$716.67" in html    # total outflows

    def test_net_positive_green(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Positive net cash flow is shown with text-success class."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Every Period")

        _create_income_template(
            user, db.session, checking, category,
            "Paycheck", "2000.00", rule,
        )
        _create_expense_template(
            user, db.session, checking, category,
            "Small Bill", "50.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        # Net is positive: income > outflows.
        assert "text-success" in html

    def test_net_negative_red(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Negative net cash flow is shown with text-danger class."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Every Period")

        # High expense, no income.
        _create_expense_template(
            user, db.session, checking, category,
            "Big Bill", "5000.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        # Net is negative: no income, all outflows.
        assert "text-danger" in html


# ── Filtering Tests ──────────────────────────────────────────────────


class TestObligationsFiltering:
    """Tests for correct inclusion/exclusion of templates."""

    def test_inactive_templates_excluded(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An is_active=False template does not appear."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Monthly", day_of_month=1)

        tmpl = _create_expense_template(
            user, db.session, checking, category,
            "Inactive Bill", "100.00", rule,
        )
        tmpl.is_active = False
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "Inactive Bill" not in html

    def test_non_recurring_templates_excluded(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A template with no recurrence rule does not appear."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        tmpl = TransactionTemplate(
            user_id=user.id,
            account_id=checking.id,
            category_id=category.id,
            recurrence_rule_id=None,
            transaction_type_id=expense_type.id,
            name="One-Time Purchase",
            default_amount=Decimal("999.00"),
        )
        db.session.add(tmpl)
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "One-Time Purchase" not in html

    def test_expense_group_correct(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An expense template appears in the expense section."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Monthly", day_of_month=15)

        _create_expense_template(
            user, db.session, checking, category,
            "Rent Payment", "1200.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "Rent Payment" in html
        assert "Recurring Expenses" in html

    def test_income_group_correct(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An income template appears in the income section."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Every Period")

        _create_income_template(
            user, db.session, checking, category,
            "Side Gig", "300.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "Side Gig" in html
        assert "Recurring Income" in html

    def test_transfer_group_correct(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A transfer template appears in the transfer section with
        both from and to account names.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        savings = _create_savings_account(user, db.session)
        rule = _create_rule(user, db.session, "Monthly", day_of_month=1)

        _create_transfer_template(
            user, db.session, checking, savings,
            "Monthly Savings", "200.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "Monthly Savings" in html
        assert checking.name in html
        assert savings.name in html

    def test_frequency_label_displayed(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The frequency label is shown for each obligation."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]
        rule = _create_rule(user, db.session, "Every Period")

        _create_expense_template(
            user, db.session, checking, category,
            "Biweekly Expense", "50.00", rule,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()
        assert "Biweekly" in html


# ── IDOR Tests ───────────────────────────────────────────────────────


class TestObligationsIDOR:
    """Tests for ownership isolation."""

    def test_only_current_user_templates(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """Only the authenticated user's templates appear.

        Creates templates for both users, verifies user 2's templates
        do not appear in user 1's obligations page.
        """
        user1 = seed_user["user"]
        user2 = second_user["user"]
        checking1 = seed_user["account"]
        checking2 = second_user["account"]
        category1 = list(seed_user["categories"].values())[0]
        category2 = list(second_user["categories"].values())[0]

        rule1 = _create_rule(user1, db.session, "Monthly", day_of_month=1)
        rule2 = _create_rule(user2, db.session, "Monthly", day_of_month=1)

        _create_expense_template(
            user1, db.session, checking1, category1,
            "My Rent", "1200.00", rule1,
        )
        _create_expense_template(
            user2, db.session, checking2, category2,
            "Their Rent", "900.00", rule2,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()

        assert "My Rent" in html
        assert "Their Rent" not in html


# ── Section Subtotal Tests ───────────────────────────────────────────


class TestObligationsSubtotals:
    """Tests for section-level subtotals."""

    def test_expense_section_subtotal(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Two expenses with different frequencies have correct section subtotal.

        Setup: $100 biweekly + $500 monthly.
        Expected subtotal: 216.67 + 500.00 = $716.67
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = list(seed_user["categories"].values())[0]

        rule_bw = _create_rule(user, db.session, "Every Period")
        rule_mo = _create_rule(user, db.session, "Monthly", day_of_month=15)

        _create_expense_template(
            user, db.session, checking, category,
            "Biweekly Bill", "100.00", rule_bw,
        )
        _create_expense_template(
            user, db.session, checking, category,
            "Monthly Bill", "500.00", rule_mo,
        )
        db.session.commit()

        resp = auth_client.get("/obligations")
        html = resp.data.decode()

        # The section subtotal should appear in the footer.
        assert "$716.67" in html
