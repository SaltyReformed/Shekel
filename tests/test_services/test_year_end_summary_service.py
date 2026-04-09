"""
Tests for year_end_summary_service.py -- Commit 13.

Verifies all six sections of the year-end summary plus the OP-2
payment timeliness integration.  Each test seeds its own data via
conftest fixtures and helper functions.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    AcctTypeEnum,
    CalcMethodEnum,
    DeductionTimingEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.ref import (
    AccountType,
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    Status,
    TaxType,
    TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.tax_config import (
    FicaConfig,
    StateTaxConfig,
    TaxBracket,
    TaxBracketSet,
)
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import amortization_engine, paycheck_calculator
from app.services.tax_config_service import load_tax_configs
from app.services.year_end_summary_service import compute_year_end_summary

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")
YEAR = 2026


# ── Helper Functions ──────────────────────────────────────────────


def _add_tax_configs(user, profile):
    """Seed TaxBracketSet, StateTaxConfig, and FicaConfig for 2026.

    Uses a simple 2-bracket progressive federal tax:
      10% on first $50,000, 22% above.
    NC flat state rate: 4.5%.
    Standard FICA rates.
    """
    bracket_set = TaxBracketSet(
        user_id=user.id,
        filing_status_id=profile.filing_status_id,
        tax_year=YEAR,
        standard_deduction=Decimal("15000.00"),
        child_credit_amount=Decimal("2000.00"),
        other_dependent_credit_amount=Decimal("500.00"),
    )
    db.session.add(bracket_set)
    db.session.flush()

    b1 = TaxBracket(
        bracket_set_id=bracket_set.id,
        min_income=Decimal("0"),
        max_income=Decimal("50000"),
        rate=Decimal("0.1000"),
        sort_order=0,
    )
    b2 = TaxBracket(
        bracket_set_id=bracket_set.id,
        min_income=Decimal("50000"),
        max_income=None,
        rate=Decimal("0.2200"),
        sort_order=1,
    )
    db.session.add_all([b1, b2])

    flat_type = db.session.query(TaxType).filter_by(name="flat").one()
    state_config = StateTaxConfig(
        user_id=user.id,
        tax_type_id=flat_type.id,
        state_code="NC",
        tax_year=YEAR,
        flat_rate=Decimal("0.0450"),
    )
    db.session.add(state_config)

    fica = FicaConfig(
        user_id=user.id,
        tax_year=YEAR,
        ss_rate=Decimal("0.0620"),
        ss_wage_base=Decimal("168600.00"),
        medicare_rate=Decimal("0.0145"),
        medicare_surtax_rate=Decimal("0.0090"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )
    db.session.add(fica)
    db.session.commit()

    return bracket_set, state_config, fica


def _create_paid_expense(
    account, scenario, period, name, amount,
    category=None, due_date_val=None, paid_at_val=None,
):
    """Create a settled (Paid) expense transaction."""
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = Transaction(
        account_id=account.id,
        scenario_id=scenario.id,
        pay_period_id=period.id,
        status_id=paid_status_id,
        transaction_type_id=expense_type_id,
        name=name,
        estimated_amount=amount,
        actual_amount=amount,
        category_id=category.id if category else None,
        due_date=due_date_val,
        paid_at=paid_at_val,
    )
    db.session.add(txn)
    return txn


def _create_projected_expense(account, scenario, period, name, amount,
                              category=None):
    """Create a projected expense transaction."""
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    txn = Transaction(
        account_id=account.id,
        scenario_id=scenario.id,
        pay_period_id=period.id,
        status_id=projected_id,
        transaction_type_id=expense_type_id,
        name=name,
        estimated_amount=amount,
        category_id=category.id if category else None,
    )
    db.session.add(txn)
    return txn


def _create_paid_income(account, scenario, period, name, amount,
                        category=None):
    """Create a settled (Received) income transaction."""
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    received_id = ref_cache.status_id(StatusEnum.RECEIVED)
    txn = Transaction(
        account_id=account.id,
        scenario_id=scenario.id,
        pay_period_id=period.id,
        status_id=received_id,
        transaction_type_id=income_type_id,
        name=name,
        estimated_amount=amount,
        actual_amount=amount,
        category_id=category.id if category else None,
    )
    db.session.add(txn)
    return txn


def _create_transfer_with_shadows(
    user, from_account, to_account, scenario, period, name, amount,
):
    """Create a transfer and its two shadow transactions.

    Mirrors transfer_service.create_transfer() shadow creation logic
    without importing the full service (which would mutate more state
    than needed for unit tests).
    """
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    transfer = Transfer(
        user_id=user.id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=scenario.id,
        status_id=paid_status_id,
        name=name,
        amount=amount,
    )
    db.session.add(transfer)
    db.session.flush()

    # Expense shadow on from_account.
    expense_shadow = Transaction(
        account_id=from_account.id,
        scenario_id=scenario.id,
        pay_period_id=period.id,
        status_id=paid_status_id,
        transaction_type_id=expense_type_id,
        name=name,
        estimated_amount=amount,
        actual_amount=amount,
        transfer_id=transfer.id,
    )
    db.session.add(expense_shadow)

    # Income shadow on to_account.
    income_shadow = Transaction(
        account_id=to_account.id,
        scenario_id=scenario.id,
        pay_period_id=period.id,
        status_id=paid_status_id,
        transaction_type_id=income_type_id,
        name=name,
        estimated_amount=amount,
        actual_amount=amount,
        transfer_id=transfer.id,
    )
    db.session.add(income_shadow)

    return transfer, expense_shadow, income_shadow


def _create_mortgage_account(user, periods):
    """Create a mortgage account with loan parameters.

    Mortgage: $240,000 at 6.5%, 30-year, originated 2025-01-01.
    """
    mortgage_type = (
        db.session.query(AccountType)
        .filter_by(name="Mortgage").one()
    )
    mortgage_acct = Account(
        user_id=user.id,
        account_type_id=mortgage_type.id,
        name="Home Mortgage",
        current_anchor_balance=Decimal("240000.00"),
        current_anchor_period_id=periods[0].id,
    )
    db.session.add(mortgage_acct)
    db.session.flush()

    params = LoanParams(
        account_id=mortgage_acct.id,
        original_principal=Decimal("240000.00"),
        current_principal=Decimal("240000.00"),
        interest_rate=Decimal("0.06500"),
        term_months=360,
        origination_date=date(2025, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.commit()

    return mortgage_acct, params


# ── C13-1: Empty Summary ─────────────────────────────────────────


class TestYearEndEmpty:
    """Tests for the year-end summary with minimal or no data."""

    def test_year_end_empty(self, app, db, seed_user, seed_periods):
        """C13-1: User with no salary, no transactions, no transfers.

        All sections should be empty or zero.  The checking account
        exists but has no activity.
        """
        user = seed_user["user"]
        result = compute_year_end_summary(user.id, YEAR)

        # Income section all zeros.
        inc = result["income_tax"]
        assert inc["gross_wages"] == ZERO
        assert inc["federal_tax"] == ZERO
        assert inc["state_tax"] == ZERO
        assert inc["social_security_tax"] == ZERO
        assert inc["medicare_tax"] == ZERO
        assert inc["net_pay_total"] == ZERO
        assert inc["mortgage_interest_total"] == ZERO
        assert inc["pretax_deductions"] == []
        assert inc["posttax_deductions"] == []

        # Spending, transfers, debt, savings -- empty.
        assert result["spending_by_category"] == []
        assert result["transfers_summary"] == []
        assert result["debt_progress"] == []
        assert result["savings_progress"] == []
        assert result["payment_timeliness"] is None

        # Net worth should exist (checking account has balance).
        nw = result["net_worth"]
        assert len(nw["monthly_values"]) == 12


# ── Income & Tax Tests ────────────────────────────────────────────


class TestIncomeTax:
    """Tests for the income/tax breakdown section."""

    def test_income_aggregation(self, app, db, seed_full_user_data):
        """C13-2: Gross wages equal the sum of per-period gross.

        Salary: $75,000/year, 10 periods in the test data.
        Gross biweekly = (75000 / 26).quantize(0.01) = $2,884.62
        Expected gross_wages = 10 * $2,884.62 = $28,846.20
        """
        data = seed_full_user_data
        user = data["user"]
        profile = data["salary_profile"]

        _add_tax_configs(user, profile)

        result = compute_year_end_summary(user.id, YEAR)
        inc = result["income_tax"]

        expected_gross_biweekly = (
            Decimal("75000") / Decimal("26")
        ).quantize(TWO_PLACES)
        expected_gross = expected_gross_biweekly * 10

        assert inc["gross_wages"] == expected_gross, (
            f"Expected gross_wages={expected_gross}, "
            f"got {inc['gross_wages']}"
        )

    def test_tax_breakdown(self, app, db, seed_full_user_data):
        """C13-3: Tax totals match sum of per-period calculator outputs.

        Calls project_salary directly and verifies the year-end
        service produces identical sums.
        """
        data = seed_full_user_data
        user = data["user"]
        profile = data["salary_profile"]
        periods = data["periods"]

        _add_tax_configs(user, profile)

        # Compute expected values via paycheck calculator directly.
        tax_configs = load_tax_configs(user.id, profile, tax_year=YEAR)
        breakdowns = paycheck_calculator.project_salary(
            profile, periods, tax_configs,
        )
        expected_federal = sum(bd.federal_tax for bd in breakdowns)
        expected_state = sum(bd.state_tax for bd in breakdowns)
        expected_ss = sum(bd.social_security for bd in breakdowns)
        expected_medicare = sum(bd.medicare for bd in breakdowns)

        result = compute_year_end_summary(user.id, YEAR)
        inc = result["income_tax"]

        assert inc["federal_tax"] == expected_federal
        assert inc["state_tax"] == expected_state
        assert inc["social_security_tax"] == expected_ss
        assert inc["medicare_tax"] == expected_medicare

    def test_income_no_salary_profile(self, app, db, seed_user,
                                      seed_periods):
        """C13-extra1: No salary profile returns all zeros, no crash."""
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        inc = result["income_tax"]
        assert inc["gross_wages"] == ZERO
        assert inc["federal_tax"] == ZERO
        assert inc["net_pay_total"] == ZERO

    def test_deductions_grouped_by_name(self, app, db,
                                        seed_full_user_data):
        """C13-extra2: Pre-tax deductions grouped by name with correct sums.

        Adds 401k ($200/period) and HSA ($50/period) deductions to
        the salary profile.  Verifies the year-end service groups
        them by name and sums across all periods.
        """
        data = seed_full_user_data
        user = data["user"]
        profile = data["salary_profile"]
        periods = data["periods"]

        _add_tax_configs(user, profile)

        # Add pre-tax deductions.
        pre_tax_timing = (
            db.session.query(DeductionTiming)
            .filter_by(name="pre_tax").one()
        )
        flat_method = (
            db.session.query(CalcMethod)
            .filter_by(name="flat").one()
        )

        ded_401k = PaycheckDeduction(
            salary_profile_id=profile.id,
            deduction_timing_id=pre_tax_timing.id,
            calc_method_id=flat_method.id,
            name="401k",
            amount=Decimal("200.0000"),
            deductions_per_year=26,
        )
        ded_hsa = PaycheckDeduction(
            salary_profile_id=profile.id,
            deduction_timing_id=pre_tax_timing.id,
            calc_method_id=flat_method.id,
            name="HSA",
            amount=Decimal("50.0000"),
            deductions_per_year=26,
        )
        db.session.add_all([ded_401k, ded_hsa])
        db.session.commit()

        # Expire the profile to reload deductions.
        db.session.expire(profile)

        result = compute_year_end_summary(user.id, YEAR)
        inc = result["income_tax"]

        num_periods = len(periods)
        pretax = {d["name"]: d["annual_total"]
                  for d in inc["pretax_deductions"]}

        assert "401k" in pretax, "401k deduction missing"
        assert pretax["401k"] == Decimal("200") * num_periods
        assert "HSA" in pretax, "HSA deduction missing"
        assert pretax["HSA"] == Decimal("50") * num_periods

    def test_net_pay_consistency(self, app, db, seed_full_user_data):
        """C13-extra3: net_pay = gross - taxes - all deductions.

        Verifies the accounting identity holds across the year.
        """
        data = seed_full_user_data
        user = data["user"]
        profile = data["salary_profile"]
        _add_tax_configs(user, profile)

        result = compute_year_end_summary(user.id, YEAR)
        inc = result["income_tax"]

        total_taxes = (
            inc["federal_tax"]
            + inc["state_tax"]
            + inc["social_security_tax"]
            + inc["medicare_tax"]
        )
        expected_net = (
            inc["gross_wages"]
            - total_taxes
            - inc["total_pretax"]
            - inc["total_posttax"]
        )
        assert inc["net_pay_total"] == expected_net, (
            f"Net pay mismatch: expected {expected_net}, "
            f"got {inc['net_pay_total']}"
        )


# ── Mortgage Interest Tests ───────────────────────────────────────


class TestMortgageInterest:
    """Tests for the mortgage interest aggregation section."""

    def test_mortgage_interest_total(self, app, db, seed_user,
                                     seed_periods):
        """C13-4: Mortgage interest matches sum of schedule interest.

        Creates a $240,000 mortgage at 6.5% originated 2025-01-01.
        Generates the theoretical schedule and verifies the year-end
        service sums the same interest for 2026.
        """
        user = seed_user["user"]
        periods = seed_periods
        mortgage_acct, params = _create_mortgage_account(user, periods)

        # Compute expected interest from the amortization engine.
        schedule = amortization_engine.generate_schedule(
            current_principal=params.original_principal,
            annual_rate=params.interest_rate,
            remaining_months=params.term_months,
            origination_date=params.origination_date,
            payment_day=params.payment_day,
            original_principal=params.original_principal,
            term_months=params.term_months,
        )
        expected_interest = sum(
            row.interest for row in schedule
            if row.payment_date.year == YEAR
        )
        assert expected_interest > ZERO, (
            "Expected non-zero interest for 2026"
        )

        result = compute_year_end_summary(user.id, YEAR)
        actual = result["income_tax"]["mortgage_interest_total"]
        assert actual == expected_interest, (
            f"Mortgage interest: expected {expected_interest}, "
            f"got {actual}"
        )

    def test_mortgage_interest_no_mortgage(self, app, db, seed_user,
                                           seed_periods):
        """C13-extra4: No mortgage accounts returns zero interest."""
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        assert result["income_tax"]["mortgage_interest_total"] == ZERO

    def test_mortgage_interest_partial_year(self, app, db, seed_user,
                                            seed_periods):
        """C13-extra5: Mortgage originated mid-year only counts months
        with payments.

        Mortgage originated 2026-07-01 -- only Jul-Dec payments
        counted for 2026.
        """
        user = seed_user["user"]
        periods = seed_periods

        mortgage_type = (
            db.session.query(AccountType)
            .filter_by(name="Mortgage").one()
        )
        mortgage_acct = Account(
            user_id=user.id,
            account_type_id=mortgage_type.id,
            name="New Mortgage",
            current_anchor_balance=Decimal("200000.00"),
            current_anchor_period_id=periods[0].id,
        )
        db.session.add(mortgage_acct)
        db.session.flush()

        params = LoanParams(
            account_id=mortgage_acct.id,
            original_principal=Decimal("200000.00"),
            current_principal=Decimal("200000.00"),
            interest_rate=Decimal("0.05000"),
            term_months=360,
            origination_date=date(2026, 7, 1),
            payment_day=1,
        )
        db.session.add(params)
        db.session.commit()

        # Expected: first payment Aug 1 2026.  5 or 6 payments in 2026.
        schedule = amortization_engine.generate_schedule(
            current_principal=params.original_principal,
            annual_rate=params.interest_rate,
            remaining_months=params.term_months,
            origination_date=params.origination_date,
            payment_day=params.payment_day,
            original_principal=params.original_principal,
            term_months=params.term_months,
        )
        expected = sum(
            r.interest for r in schedule if r.payment_date.year == YEAR
        )

        result = compute_year_end_summary(user.id, YEAR)
        actual = result["income_tax"]["mortgage_interest_total"]
        assert actual == expected
        # Partial year should have less interest than full year.
        assert actual < Decimal("10000")


# ── Spending by Category Tests ────────────────────────────────────


class TestSpendingByCategory:
    """Tests for the spending categorization section."""

    def test_spending_by_category(self, app, db, seed_user, seed_periods):
        """C13-5: Three paid expenses in two categories grouped correctly.

        Creates 3 transactions:
          Home:Rent $1200 + Auto:Car Payment $350 + Home:Rent $1200.
        Expected: Home group total = $2400, Auto group total = $350.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "Rent Jan",
            Decimal("1200.00"), cats["Rent"],
        )
        _create_paid_expense(
            account, scenario, periods[1], "Car Payment Jan",
            Decimal("350.00"), cats["Car Payment"],
        )
        _create_paid_expense(
            account, scenario, periods[2], "Rent Feb",
            Decimal("1200.00"), cats["Rent"],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]

        assert len(spending) == 2
        home_group = next(g for g in spending if g["group_name"] == "Home")
        auto_group = next(g for g in spending if g["group_name"] == "Auto")
        assert home_group["group_total"] == Decimal("2400.00")
        assert auto_group["group_total"] == Decimal("350.00")

    def test_spending_hierarchy(self, app, db, seed_user, seed_periods):
        """C13-6: Group total equals sum of item totals.

        Two items in the same group:
          Home:Rent $1000 + Home:Insurance $200.
        Group total should be $1200.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods

        from app.models.category import Category
        insurance_cat = Category(
            user_id=user.id, group_name="Home", item_name="Insurance",
        )
        db.session.add(insurance_cat)
        db.session.flush()

        _create_paid_expense(
            account, scenario, periods[0], "Rent",
            Decimal("1000.00"), data["categories"]["Rent"],
        )
        _create_paid_expense(
            account, scenario, periods[0], "Insurance",
            Decimal("200.00"), insurance_cat,
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]
        home = next(g for g in spending if g["group_name"] == "Home")

        assert home["group_total"] == Decimal("1200.00")
        items_sum = sum(i["item_total"] for i in home["items"])
        assert home["group_total"] == items_sum

    def test_spending_excludes_projected(self, app, db, seed_user,
                                         seed_periods):
        """C13-extra6: Only settled transactions appear in spending.

        One paid ($500), one projected ($300).  Only the paid one
        should appear.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "Paid Rent",
            Decimal("500.00"), cats["Rent"],
        )
        _create_projected_expense(
            account, scenario, periods[1], "Projected Rent",
            Decimal("300.00"), cats["Rent"],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]
        total = sum(g["group_total"] for g in spending)
        assert total == Decimal("500.00")

    def test_spending_excludes_income(self, app, db, seed_user,
                                      seed_periods):
        """C13-extra7: Income transactions do not appear in spending.

        One paid expense ($500) and one received income ($2000).
        Only the expense should appear.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "Groceries",
            Decimal("500.00"), cats["Groceries"],
        )
        _create_paid_income(
            account, scenario, periods[0], "Paycheck",
            Decimal("2000.00"), cats["Salary"],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]
        total = sum(g["group_total"] for g in spending)
        assert total == Decimal("500.00")

    def test_spending_sorted_by_total(self, app, db, seed_user,
                                      seed_periods):
        """C13-extra8: Groups sorted descending by group_total.

        Three groups with totals: Home=$2000, Auto=$500, Family=$100.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "Rent",
            Decimal("2000.00"), cats["Rent"],
        )
        _create_paid_expense(
            account, scenario, periods[1], "Car",
            Decimal("500.00"), cats["Car Payment"],
        )
        _create_paid_expense(
            account, scenario, periods[2], "Food",
            Decimal("100.00"), cats["Groceries"],
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]
        totals = [g["group_total"] for g in spending]
        assert totals == sorted(totals, reverse=True)
        assert totals[0] == Decimal("2000.00")

    def test_spending_uses_effective_amount(self, app, db, seed_user,
                                            seed_periods):
        """C13-extra9: Uses effective_amount (actual for paid txns).

        Transaction with estimated=$100, actual=$120.
        Spending should use $120 (the actual_amount).
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        paid_status_id = ref_cache.status_id(StatusEnum.DONE)
        expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        txn = Transaction(
            account_id=account.id,
            scenario_id=scenario.id,
            pay_period_id=periods[0].id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name="Adjusted Rent",
            estimated_amount=Decimal("100.00"),
            actual_amount=Decimal("120.00"),
            category_id=cats["Rent"].id,
        )
        db.session.add(txn)
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        spending = result["spending_by_category"]
        total = sum(g["group_total"] for g in spending)
        assert total == Decimal("120.00")


# ── Transfers Summary Tests ───────────────────────────────────────


class TestTransfersSummary:
    """Tests for the transfers summary section."""

    def test_transfers_grouped_by_destination(self, app, db, seed_user,
                                              seed_periods):
        """C13-7: Transfers grouped by destination account.

        Two transfers to savings ($200 each) and one to a mortgage
        account ($1500).
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods

        # Create savings account.
        savings_type = (
            db.session.query(AccountType)
            .filter_by(name="Savings").one()
        )
        savings_acct = Account(
            user_id=user.id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("0"),
            current_anchor_period_id=periods[0].id,
        )
        db.session.add(savings_acct)

        mortgage_type = (
            db.session.query(AccountType)
            .filter_by(name="Mortgage").one()
        )
        mortgage_acct = Account(
            user_id=user.id,
            account_type_id=mortgage_type.id,
            name="Mortgage",
            current_anchor_balance=Decimal("200000.00"),
            current_anchor_period_id=periods[0].id,
        )
        db.session.add(mortgage_acct)
        db.session.flush()

        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[0],
            "Save Jan", Decimal("200.00"),
        )
        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[1],
            "Save Feb", Decimal("200.00"),
        )
        _create_transfer_with_shadows(
            user, account, mortgage_acct, scenario, periods[0],
            "Mortgage Jan", Decimal("1500.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        transfers = result["transfers_summary"]

        assert len(transfers) == 2
        by_name = {t["destination_account"]: t for t in transfers}
        assert by_name["Savings"]["total_amount"] == Decimal("400.00")
        assert by_name["Mortgage"]["total_amount"] == Decimal("1500.00")

    def test_transfers_sorted_by_total(self, app, db, seed_user,
                                       seed_periods):
        """C13-extra10: Transfers sorted descending by total_amount.

        Three destinations with different totals.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods

        # Create 3 destination accounts.
        savings_type = (
            db.session.query(AccountType)
            .filter_by(name="Savings").one()
        )
        for name, amount, idx in [
            ("Savings A", Decimal("100.00"), 0),
            ("Savings B", Decimal("500.00"), 1),
            ("Savings C", Decimal("300.00"), 2),
        ]:
            dest = Account(
                user_id=user.id,
                account_type_id=savings_type.id,
                name=name,
                current_anchor_balance=ZERO,
                current_anchor_period_id=periods[0].id,
            )
            db.session.add(dest)
            db.session.flush()
            _create_transfer_with_shadows(
                user, account, dest, scenario, periods[idx],
                f"Transfer to {name}", amount,
            )

        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        transfers = result["transfers_summary"]
        totals = [t["total_amount"] for t in transfers]
        assert totals == sorted(totals, reverse=True)


# ── Net Worth Tests ───────────────────────────────────────────────


class TestNetWorth:
    """Tests for the net worth trend section."""

    def test_net_worth_12_points(self, app, db, seed_user, seed_periods):
        """C13-8: Net worth has exactly 12 monthly entries."""
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        nw = result["net_worth"]
        assert len(nw["monthly_values"]) == 12
        months = [v["month"] for v in nw["monthly_values"]]
        assert months == list(range(1, 13))

    def test_net_worth_jan_dec_delta(self, app, db, seed_user,
                                     seed_periods):
        """C13-extra11: delta equals dec31 minus jan1."""
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        nw = result["net_worth"]
        assert nw["delta"] == nw["dec31"] - nw["jan1"]

    def test_net_worth_debt_negative(self, app, db, seed_user,
                                     seed_periods):
        """C13-extra12: Mortgage balance reduces net worth.

        Checking ($1000) + Mortgage ($240,000 liability) should yield
        negative net worth at a point where both have balances.
        """
        user = seed_user["user"]
        periods = seed_periods
        _create_mortgage_account(user, periods)

        result = compute_year_end_summary(user.id, YEAR)
        nw = result["net_worth"]

        # With a $240k mortgage and $1k checking, net worth should
        # be strongly negative at any month where both are active.
        has_negative = any(
            v["balance"] < ZERO for v in nw["monthly_values"]
        )
        assert has_negative, (
            "Expected negative net worth with $240k mortgage vs $1k checking"
        )

    def test_net_worth_empty_months_use_last_known(
        self, app, db, seed_user, seed_periods,
    ):
        """C13-extra13: Months without periods carry forward last known balance.

        With 10 periods (about 5 months of data), months 6-12 should
        use the last known net worth value.
        """
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        nw = result["net_worth"]
        values = nw["monthly_values"]

        # Find the last month with a distinct balance computation.
        # Subsequent months should repeat that value.
        last_computed = None
        for v in values:
            if v["balance"] != ZERO:
                last_computed = v["balance"]

        # Check that later months (beyond period coverage) carry
        # forward a non-zero balance if any month had data.
        if last_computed is not None:
            # The last value in the list should equal the last
            # computed value (carried forward).
            assert values[11]["balance"] == last_computed


# ── Debt Progress Tests ───────────────────────────────────────────


class TestDebtProgress:
    """Tests for the debt progress section."""

    def test_debt_progress(self, app, db, seed_user, seed_periods):
        """C13-9: Debt progress shows principal paid.

        Creates a mortgage and verifies jan1_balance > dec31_balance
        (or they are equal if no amortization data in year range).
        Principal paid = jan1 - dec31.
        """
        user = seed_user["user"]
        periods = seed_periods
        _create_mortgage_account(user, periods)

        result = compute_year_end_summary(user.id, YEAR)
        debt = result["debt_progress"]

        assert len(debt) == 1
        entry = debt[0]
        assert entry["account_name"] == "Home Mortgage"
        assert entry["principal_paid"] == (
            entry["jan1_balance"] - entry["dec31_balance"]
        )

    def test_debt_no_accounts(self, app, db, seed_user, seed_periods):
        """C13-extra14: No debt accounts returns empty list."""
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        assert result["debt_progress"] == []


# ── Savings Progress Tests ────────────────────────────────────────


class TestSavingsProgress:
    """Tests for the savings progress section."""

    def test_savings_progress(self, app, db, seed_full_user_data):
        """C13-10: Savings progress shows contributions.

        Uses the savings account from seed_full_user_data (Savings,
        $500 anchor).  Creates a transfer to it and verifies
        contributions are tracked.
        """
        data = seed_full_user_data
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        savings_acct = data["savings_account"]
        periods = data["periods"]

        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[1],
            "Monthly Savings", Decimal("200.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]

        # Find the savings account entry.
        entry = next(
            (s for s in savings if s["account_name"] == "Savings"),
            None,
        )
        assert entry is not None, "Savings account not in savings_progress"
        assert entry["total_contributions"] == Decimal("200.00")

    def test_savings_contributions_from_shadows(
        self, app, db, seed_full_user_data,
    ):
        """C13-extra15: Contributions equal sum of shadow income txns.

        Creates multiple transfers to savings and verifies total
        contributions matches the sum.
        """
        data = seed_full_user_data
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        savings_acct = data["savings_account"]
        periods = data["periods"]

        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[0],
            "Save 1", Decimal("100.00"),
        )
        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[1],
            "Save 2", Decimal("150.00"),
        )
        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[2],
            "Save 3", Decimal("250.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "Savings"
        )
        expected = Decimal("100.00") + Decimal("150.00") + Decimal("250.00")
        assert entry["total_contributions"] == expected


# ── Payment Timeliness Tests (OP-2) ──────────────────────────────


class TestPaymentTimeliness:
    """Tests for the OP-2 payment timeliness section."""

    def test_payment_timeliness_on_time(self, app, db, seed_user,
                                        seed_periods):
        """C13-op2-1: All bills paid before due date.

        2 bills paid on time.  paid_on_time = 2, paid_late = 0.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "Rent Jan",
            Decimal("1200.00"), cats["Rent"],
            due_date_val=date(2026, 1, 15),
            paid_at_val=datetime(2026, 1, 10, tzinfo=timezone.utc),
        )
        _create_paid_expense(
            account, scenario, periods[1], "Rent Feb",
            Decimal("1200.00"), cats["Rent"],
            due_date_val=date(2026, 1, 28),
            paid_at_val=datetime(2026, 1, 25, tzinfo=timezone.utc),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        pt = result["payment_timeliness"]

        assert pt is not None
        assert pt["total_bills_paid"] == 2
        assert pt["paid_on_time"] == 2
        assert pt["paid_late"] == 0

    def test_payment_timeliness_late(self, app, db, seed_user,
                                     seed_periods):
        """C13-op2-2: Bills paid after due_date counted as late.

        1 on time, 1 late.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "On Time",
            Decimal("500.00"), cats["Rent"],
            due_date_val=date(2026, 1, 15),
            paid_at_val=datetime(2026, 1, 10, tzinfo=timezone.utc),
        )
        _create_paid_expense(
            account, scenario, periods[1], "Late Bill",
            Decimal("300.00"), cats["Rent"],
            due_date_val=date(2026, 1, 20),
            paid_at_val=datetime(2026, 1, 25, tzinfo=timezone.utc),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        pt = result["payment_timeliness"]

        assert pt["total_bills_paid"] == 2
        assert pt["paid_on_time"] == 1
        assert pt["paid_late"] == 1

    def test_payment_timeliness_avg_days(self, app, db, seed_user,
                                         seed_periods):
        """C13-op2-3: avg_days_before_due matches hand computation.

        Bill 1: due Jan 15, paid Jan 10 -> 5 days early.
        Bill 2: due Jan 28, paid Jan 25 -> 3 days early.
        Average = (5 + 3) / 2 = 4.00 days.
        """
        data = seed_user
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        cats = data["categories"]

        _create_paid_expense(
            account, scenario, periods[0], "Bill A",
            Decimal("100.00"), cats["Rent"],
            due_date_val=date(2026, 1, 15),
            paid_at_val=datetime(2026, 1, 10, tzinfo=timezone.utc),
        )
        _create_paid_expense(
            account, scenario, periods[1], "Bill B",
            Decimal("100.00"), cats["Rent"],
            due_date_val=date(2026, 1, 28),
            paid_at_val=datetime(2026, 1, 25, tzinfo=timezone.utc),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        pt = result["payment_timeliness"]

        # (5 + 3) / 2 = 4.00
        assert pt["avg_days_before_due"] == Decimal("4.00")

    def test_payment_timeliness_no_data(self, app, db, seed_user,
                                        seed_periods):
        """C13-op2-4: No txns with paid_at and due_date returns None."""
        # Create an expense without paid_at/due_date.
        data = seed_user
        account = data["account"]
        scenario = data["scenario"]
        periods = seed_periods
        _create_paid_expense(
            account, scenario, periods[0], "No Dates",
            Decimal("500.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(data["user"].id, YEAR)
        assert result["payment_timeliness"] is None


# ── Integration Test ──────────────────────────────────────────────


class TestIntegration:
    """End-to-end integration test with rich data."""

    def test_full_year_end_integration(self, app, db,
                                       seed_full_user_data):
        """C13-extra16: All sections populated with rich fixture data.

        Uses seed_full_user_data (salary profile, savings account,
        transfer template) plus additional mortgage, transfers, and
        paid expenses.  Verifies all six sections are populated and
        the service does not crash.
        """
        data = seed_full_user_data
        user = data["user"]
        account = data["account"]
        scenario = data["scenario"]
        savings_acct = data["savings_account"]
        periods = data["periods"]
        profile = data["salary_profile"]
        cats = data["categories"]

        # Add tax configs for income/tax section.
        _add_tax_configs(user, profile)

        # Add a mortgage for debt/mortgage interest sections.
        mortgage_acct, _ = _create_mortgage_account(user, periods)

        # Add paid expenses for spending section.
        _create_paid_expense(
            account, scenario, periods[0], "Groceries",
            Decimal("250.00"), cats["Groceries"],
            due_date_val=date(2026, 1, 10),
            paid_at_val=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )
        _create_paid_expense(
            account, scenario, periods[1], "Car Insurance",
            Decimal("180.00"), cats["Car Payment"],
        )

        # Add transfers for transfers/savings sections.
        _create_transfer_with_shadows(
            user, account, savings_acct, scenario, periods[0],
            "Savings Transfer", Decimal("500.00"),
        )
        _create_transfer_with_shadows(
            user, account, mortgage_acct, scenario, periods[0],
            "Mortgage Payment", Decimal("1500.00"),
        )

        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)

        # Verify all sections exist and have reasonable data.
        assert result["income_tax"]["gross_wages"] > ZERO
        assert result["income_tax"]["mortgage_interest_total"] > ZERO
        assert len(result["spending_by_category"]) > 0
        assert len(result["transfers_summary"]) > 0
        assert len(result["net_worth"]["monthly_values"]) == 12
        assert len(result["debt_progress"]) == 1
        assert len(result["savings_progress"]) >= 1

        # Verify net pay consistency.
        inc = result["income_tax"]
        total_taxes = (
            inc["federal_tax"]
            + inc["state_tax"]
            + inc["social_security_tax"]
            + inc["medicare_tax"]
        )
        expected_net = (
            inc["gross_wages"]
            - total_taxes
            - inc["total_pretax"]
            - inc["total_posttax"]
        )
        assert inc["net_pay_total"] == expected_net

        # Verify net worth delta is consistent.
        nw = result["net_worth"]
        assert nw["delta"] == nw["dec31"] - nw["jan1"]
