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
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
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


def _create_investment_account(
    user, periods,
    employer_type="none",
    match_pct=None,
    match_cap_pct=None,
    flat_pct=None,
):
    """Create an investment account (401k) with InvestmentParams.

    Default: $10,000 balance, 7% assumed annual return, no employer.
    Employer settings are configurable for testing match and flat_pct.

    Returns:
        Tuple of (account, investment_params).
    """
    inv_type = (
        db.session.query(AccountType)
        .filter_by(name="401(k)").one()
    )
    inv_acct = Account(
        user_id=user.id,
        account_type_id=inv_type.id,
        name="401k",
        current_anchor_balance=Decimal("10000.00"),
        current_anchor_period_id=periods[0].id,
    )
    db.session.add(inv_acct)
    db.session.flush()

    inv_params = InvestmentParams(
        account_id=inv_acct.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type=employer_type,
        employer_match_percentage=match_pct,
        employer_match_cap_percentage=match_cap_pct,
        employer_flat_percentage=flat_pct,
    )
    db.session.add(inv_params)
    db.session.flush()

    return inv_acct, inv_params


def _create_hysa_account(user, periods):
    """Create a HYSA account with InterestParams.

    HYSA: $5,000 balance, 5% APY, daily compounding.

    Returns:
        Account object.
    """
    hysa_type = (
        db.session.query(AccountType)
        .filter_by(name="HYSA").one()
    )
    hysa_acct = Account(
        user_id=user.id,
        account_type_id=hysa_type.id,
        name="High Yield Savings",
        current_anchor_balance=Decimal("5000.00"),
        current_anchor_period_id=periods[0].id,
    )
    db.session.add(hysa_acct)
    db.session.flush()

    interest_params = InterestParams(
        account_id=hysa_acct.id,
        apy=Decimal("0.05000"),
        compounding_frequency="daily",
    )
    db.session.add(interest_params)
    db.session.flush()

    return hysa_acct


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

    def test_net_worth_debt_uses_amortization(
        self, app, db, seed_user, seed_periods,
    ):
        """C1-11: Net worth reflects amortization-based debt balances.

        With a $240k mortgage (originated 2025-01-01) and $1k checking,
        net worth should use the amortization schedule balance for the
        mortgage, not the static anchor balance ($240k).

        The amortization schedule reduces the mortgage balance each
        month.  By month 1 (Jan 2026) the balance is ~$237,548.
        Net worth at month 1 ~ $1,000 - $237,548 = -$236,548.
        If the old anchor-only calculator were used, net worth would
        be $1,000 - $240,000 = -$239,000 every month.
        """
        user = seed_user["user"]
        periods = seed_periods
        _create_mortgage_account(user, periods)

        result = compute_year_end_summary(user.id, YEAR)
        nw = result["net_worth"]

        # Net worth should be negative (mortgage > checking).
        month_1 = nw["monthly_values"][0]["balance"]
        assert month_1 < ZERO

        # With amortization, the mortgage balance at Jan 2026 is ~$237,548
        # (less than the $240k anchor).  So net worth should be less
        # negative than $1,000 - $240,000 = -$239,000.
        static_nw = Decimal("1000.00") - Decimal("240000.00")
        assert month_1 > static_nw, (
            f"Net worth {month_1} should be less negative than "
            f"static {static_nw} because amortization reduces the "
            f"mortgage balance below $240k"
        )

        # Consecutive months with data should show improving net worth
        # (less negative) as principal decreases via amortization.
        non_zero_months = [
            v for v in nw["monthly_values"]
            if v["balance"] != ZERO
        ]
        if len(non_zero_months) >= 2:
            assert non_zero_months[-1]["balance"] >= non_zero_months[0]["balance"]

    def test_net_worth_investment_includes_growth(
        self, app, db, seed_full_user_data,
    ):
        """C1-1: Investment account growth reflected in net worth.

        Creates a 401(k) ($10,000, 7% annual return, no employer) plus
        the existing checking ($1,000) and savings ($500).  Net worth
        should increase over time as investment growth accumulates.
        Without the fix, the 401(k) balance would be flat at $10,000.

        seed_full_user_data also creates a $1,200 projected rent expense
        that reduces checking, so we compare growth over time rather
        than against a static anchor sum.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        _create_investment_account(user, periods, employer_type="none")
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        nw = result["net_worth"]

        non_zero = [
            v for v in nw["monthly_values"] if v["balance"] != ZERO
        ]
        assert len(non_zero) > 0, "Expected non-zero net worth values"

        # Net worth should increase over time because the 401(k) is
        # growing at 7%.  Without investment growth, net worth would
        # be flat (checking balance is static after the rent expense).
        if len(non_zero) >= 2:
            assert non_zero[-1]["balance"] > non_zero[0]["balance"], (
                "Net worth should increase over time due to 401(k) growth"
            )

    def test_net_worth_investment_with_employer(
        self, app, db, seed_full_user_data,
    ):
        """C1-2: Employer contributions increase net worth further.

        Creates a 401(k) with flat 3% employer contribution and a
        salary profile.  Net worth growth should exceed the growth-only
        case because employer money is added on top of investment returns.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        _create_investment_account(
            user, periods,
            employer_type="flat_percentage",
            flat_pct=Decimal("0.0300"),
        )
        db.session.commit()

        result_employer = compute_year_end_summary(user.id, YEAR)
        nw = result_employer["net_worth"]

        non_zero = [
            v for v in nw["monthly_values"] if v["balance"] != ZERO
        ]
        assert len(non_zero) > 0

        # Net worth should increase over time due to both growth and
        # employer contributions.
        if len(non_zero) >= 2:
            assert non_zero[-1]["balance"] > non_zero[0]["balance"], (
                "Net worth should increase with employer contributions"
            )

    def test_net_worth_mixed_account_types(
        self, app, db, seed_full_user_data,
    ):
        """C1-3: Mixed accounts each use correct calculation path.

        Checking ($1k) + 401(k) ($10k, 7%) + HYSA ($5k, 5% APY) +
        Mortgage ($240k).  All four types contribute correctly.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        _create_investment_account(user, periods, employer_type="none")
        _create_hysa_account(user, periods)
        _create_mortgage_account(user, periods)
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        nw = result["net_worth"]

        # With a $240k mortgage, net worth should be strongly negative.
        non_zero = [
            v for v in nw["monthly_values"] if v["balance"] != ZERO
        ]
        assert len(non_zero) > 0
        assert non_zero[0]["balance"] < ZERO

        # But net worth should improve over time: mortgage balance
        # decreases (amortization), 401k grows (returns), HYSA grows
        # (interest).  All three push net worth up.
        if len(non_zero) >= 2:
            assert non_zero[-1]["balance"] > non_zero[0]["balance"], (
                "Net worth should improve as investments grow and "
                "mortgage principal decreases"
            )

    def test_net_worth_consistent_with_savings_progress(
        self, app, db, seed_full_user_data,
    ):
        """C1-4: Investment Dec 31 in net worth matches savings progress.

        Both sections should use the growth engine for investment
        accounts, producing consistent Dec 31 balances.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        _create_investment_account(user, periods, employer_type="none")
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)

        # Get savings progress Dec 31 for the 401k.
        sp_entry = next(
            s for s in result["savings_progress"]
            if s["account_name"] == "401k"
        )
        sp_dec31 = sp_entry["dec31_balance"]

        # Get net worth Dec 31 -- it includes checking + savings + 401k.
        # We cannot isolate the 401k balance from net worth directly,
        # but we can verify that savings progress reports growth and
        # that net worth exceeds the static sum.
        assert sp_dec31 > Decimal("10000.00"), (
            "Savings progress should show 401(k) growth above anchor"
        )

        # Net worth Dec 31 should be at least as high as savings
        # progress Dec 31 for the 401k (since checking and savings
        # accounts add positive value).
        assert result["net_worth"]["dec31"] >= sp_dec31 - Decimal("10000.00")


# ── Debt Progress Tests ───────────────────────────────────────────


class TestDebtProgress:
    """Tests for the debt progress section.

    All debt progress tests use amortization-engine-based calculations.
    The expected values are hand-computed from the standard amortization
    formula: M = P * [r(1+r)^n] / [(1+r)^n - 1].
    """

    def test_debt_progress_uses_amortization(
        self, app, db, seed_user, seed_periods,
    ):
        """C1-6: Debt progress balances match the amortization schedule.

        Mortgage: $240,000 at 6.5%, 30-year, originated 2025-01-01.
        Monthly payment: $1,516.96 (from amortization formula).

        Jan 1 2026 balance = balance after Dec 2025 payment = $237,547.74
        (11 payments of principal in 2025: each reducing balance from
        the first $216.96 to the eleventh payment's ~$241.15).

        Dec 31 2026 balance = balance after Dec 2026 payment = $234,701.02
        (12 more payments of principal in 2026).

        Principal paid in 2026 = $237,547.74 - $234,701.02 = $2,846.72.
        """
        user = seed_user["user"]
        periods = seed_periods
        _create_mortgage_account(user, periods)

        result = compute_year_end_summary(user.id, YEAR)
        debt = result["debt_progress"]

        assert len(debt) == 1
        entry = debt[0]
        assert entry["account_name"] == "Home Mortgage"

        # Verify exact amortization-based values.
        assert entry["jan1_balance"] == Decimal("237547.74")
        assert entry["dec31_balance"] == Decimal("234701.02")
        assert entry["principal_paid"] == Decimal("2846.72")
        # Invariant: principal_paid = jan1 - dec31.
        assert entry["principal_paid"] == (
            entry["jan1_balance"] - entry["dec31_balance"]
        )

    def test_debt_progress_with_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """C1-7: Debt progress reflects actual payment history.

        Creates a mortgage and makes 3 transfer payments of $1,800 each
        (above the standard $1,516.96 P&I).  The extra ~$283 per payment
        goes to principal, accelerating paydown.  Dec 31 balance should
        be lower than if only standard payments were made.
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        periods = seed_periods
        mortgage_acct, _ = _create_mortgage_account(user, periods)

        # Make 3 payments via transfers (shadow income on mortgage).
        for i in range(3):
            _create_transfer_with_shadows(
                user, account, mortgage_acct, scenario,
                periods[i + 1],
                f"Mortgage Payment {i + 1}",
                Decimal("1800.00"),
            )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        debt = result["debt_progress"]

        assert len(debt) == 1
        entry = debt[0]

        # With extra payments, more principal is paid off.
        assert entry["principal_paid"] > ZERO
        assert entry["jan1_balance"] > entry["dec31_balance"]
        # Invariant still holds.
        assert entry["principal_paid"] == (
            entry["jan1_balance"] - entry["dec31_balance"]
        )

    def test_debt_progress_escrow_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """C1-8: Escrow is subtracted from payments before amortization.

        Creates a mortgage with a $283/month escrow component, then
        makes a $1,800 payment.  The engine should subtract escrow
        ($283) before computing principal/interest split, so the
        effective payment for amortization is ~$1,517 (standard P&I).
        """
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]
        periods = seed_periods
        mortgage_acct, _ = _create_mortgage_account(user, periods)

        # Add escrow component to the mortgage.
        from app.models.loan_features import EscrowComponent  # pylint: disable=import-outside-toplevel
        escrow = EscrowComponent(
            account_id=mortgage_acct.id,
            name="Property Tax",
            annual_amount=Decimal("3396.00"),
            is_active=True,
        )
        db.session.add(escrow)

        # Make one payment that includes escrow.
        _create_transfer_with_shadows(
            user, account, mortgage_acct, scenario,
            periods[1],
            "Mortgage + Escrow",
            Decimal("1800.00"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        debt = result["debt_progress"]

        assert len(debt) == 1
        entry = debt[0]

        # Principal paid should reflect escrow-subtracted payments,
        # not raw $1,800 payments.  With proper escrow subtraction,
        # the effective P&I is ~$1,517 and principal is only ~$217.
        # Without escrow subtraction, $283 extra would be incorrectly
        # treated as additional principal.
        assert entry["principal_paid"] > ZERO
        assert entry["jan1_balance"] > entry["dec31_balance"]

    def test_debt_no_accounts(self, app, db, seed_user, seed_periods):
        """C13-extra14: No debt accounts returns empty list."""
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        assert result["debt_progress"] == []

    def test_mortgage_interest_with_prepared_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """C1-10: Mortgage interest uses prepared schedule.

        Verifies the mortgage interest total uses the same prepared
        amortization schedule as debt progress.  For a $240k mortgage
        at 6.5% with no extra payments, total 2026 interest should
        be $15,356.80 (12 months of interest on declining principal).
        """
        user = seed_user["user"]
        _create_mortgage_account(user, seed_periods)

        result = compute_year_end_summary(user.id, YEAR)
        interest = result["income_tax"]["mortgage_interest_total"]

        # 12 months of interest in 2026 on a $240k @ 6.5% mortgage.
        assert interest == Decimal("15356.80")


# ── Savings Progress Tests ────────────────────────────────────────


class TestSavingsProgress:
    """Tests for the savings progress section.

    Covers three calculation paths: plain savings (balance calculator),
    investment accounts (growth engine with employer/returns), and
    interest-bearing accounts (balance calculator with interest).
    """

    def test_savings_progress_basic(self, app, db, seed_full_user_data):
        """C2-1: Plain savings uses balance calculator and includes new fields.

        Uses the savings account from seed_full_user_data (Savings,
        $500 anchor).  Creates a transfer to it and verifies
        contributions are tracked.  Employer and growth are zero for
        plain savings.
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

        entry = next(
            (s for s in savings if s["account_name"] == "Savings"),
            None,
        )
        assert entry is not None, "Savings account not in savings_progress"
        assert entry["total_contributions"] == Decimal("200.00")
        # Plain savings: no employer match, no growth.
        assert entry["employer_contributions"] == ZERO
        assert entry["investment_growth"] == ZERO

    def test_savings_contributions_from_shadows(
        self, app, db, seed_full_user_data,
    ):
        """C2-2: Contributions equal sum of shadow income txns.

        Creates multiple transfers to savings and verifies total
        contributions matches the sum.  New fields present and zero.
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
        assert entry["employer_contributions"] == ZERO
        assert entry["investment_growth"] == ZERO

    def test_savings_investment_with_growth(
        self, app, db, seed_full_user_data,
    ):
        """C2-3: Investment account includes growth from assumed annual return.

        Creates a 401k account with InvestmentParams (7% annual return,
        no employer match) and $10,000 balance.  Dec 31 balance should
        include growth from the assumed annual return.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        inv_acct, _ = _create_investment_account(
            user, periods, employer_type="none",
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            (s for s in savings if s["account_name"] == "401k"),
            None,
        )
        assert entry is not None, "401k not in savings_progress"
        # Growth should be > 0 from assumed 7% annual return.
        assert entry["investment_growth"] > ZERO
        # Dec 31 should exceed Jan 1 by at least the growth amount.
        assert entry["dec31_balance"] > entry["jan1_balance"]
        assert entry["employer_contributions"] == ZERO

    def test_savings_employer_match(
        self, app, db, seed_full_user_data,
    ):
        """C2-4: Investment account includes employer match contributions.

        Creates a 401k with employer match (50% up to 6% of salary)
        and a paycheck deduction of $200/period.  Employer match should
        produce non-zero employer_contributions.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]
        salary_profile = data["salary_profile"]

        inv_acct, inv_params = _create_investment_account(
            user, periods,
            employer_type="match",
            match_pct=Decimal("0.5000"),
            match_cap_pct=Decimal("0.0600"),
        )

        # Add paycheck deduction targeting the 401k.
        flat_method = (
            db.session.query(CalcMethod)
            .filter_by(name="flat").one()
        )
        pre_tax_timing = (
            db.session.query(DeductionTiming)
            .filter_by(name="pre_tax").one()
        )
        ded = PaycheckDeduction(
            salary_profile_id=salary_profile.id,
            target_account_id=inv_acct.id,
            name="401k Contribution",
            amount=Decimal("200.00"),
            calc_method_id=flat_method.id,
            deduction_timing_id=pre_tax_timing.id,
            is_active=True,
        )
        db.session.add(ded)
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "401k"
        )
        # Employer match should be > 0.
        assert entry["employer_contributions"] > ZERO
        # Dec 31 should include employee + employer + growth.
        assert entry["dec31_balance"] > entry["jan1_balance"]
        assert entry["investment_growth"] > ZERO

    def test_savings_employer_flat_pct(
        self, app, db, seed_full_user_data,
    ):
        """C2-5: Investment account with flat percentage employer contribution.

        Creates a 401k with employer_flat_percentage=3%.  The employer
        contributes 3% of gross biweekly pay each period regardless of
        employee contribution.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        inv_acct, _ = _create_investment_account(
            user, periods,
            employer_type="flat_percentage",
            flat_pct=Decimal("0.0300"),
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "401k"
        )
        assert entry["employer_contributions"] > ZERO

    def test_savings_hysa_with_interest(
        self, app, db, seed_full_user_data,
    ):
        """C2-6: HYSA account includes interest in growth field.

        Creates a HYSA with 5% APY and verifies interest accrual is
        reflected in the investment_growth field.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        hysa_acct = _create_hysa_account(user, periods)
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            (s for s in savings if s["account_name"] == "High Yield Savings"),
            None,
        )
        assert entry is not None, "HYSA not in savings_progress"
        # Interest-bearing: growth = total interest earned.
        assert entry["investment_growth"] > ZERO
        assert entry["employer_contributions"] == ZERO
        # Dec 31 balance includes interest accrual.
        assert entry["dec31_balance"] > entry["jan1_balance"]

    def test_savings_no_accounts(self, app, db, seed_user, seed_periods):
        """C2-7: No savings accounts returns empty list."""
        # seed_user only has a checking account, no savings.
        # But we need to ensure no savings accounts exist beyond checking.
        result = compute_year_end_summary(seed_user["user"].id, YEAR)
        assert result["savings_progress"] == []

    def test_savings_mixed_accounts(
        self, app, db, seed_full_user_data,
    ):
        """C2-8: Mixed account types each use correct calculation path.

        Creates a plain savings (from seed), an investment 401k, and
        a HYSA.  All three appear with correct calculation paths.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        _create_investment_account(user, periods, employer_type="none")
        _create_hysa_account(user, periods)
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        names = {s["account_name"] for s in savings}

        assert "Savings" in names, "Plain savings missing"
        assert "401k" in names, "Investment account missing"
        assert "High Yield Savings" in names, "HYSA missing"

        # Each entry has all required fields.
        for entry in savings:
            assert "employer_contributions" in entry
            assert "investment_growth" in entry
            assert "total_contributions" in entry


# ── Pre-Anchor Savings Progress Tests ────────────────────────────


class TestSavingsProgressPreAnchor:
    """Tests for savings progress when the anchor period is after January 1.

    The balance calculator skips pre-anchor periods, so
    _lookup_period_balance returns ZERO for January when the anchor
    is later in the year.  These tests verify the fix that
    reverse-projects from the anchor balance to derive the correct
    January 1 balance.
    """

    def test_investment_pre_anchor_jan1_balance(
        self, app, db, seed_full_user_data,
    ):
        """Investment account with mid-year anchor has non-zero Jan 1 balance.

        Anchor at period 5 (~March 2026).  The reverse projection should
        derive a Jan 1 balance that is positive and less than the anchor
        balance (growth increased the balance from Jan to March).
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        inv_acct, _ = _create_investment_account(
            user, periods, employer_type="none",
        )
        # Move anchor to period 5 (mid-year).
        inv_acct.current_anchor_period_id = periods[5].id
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "401k"
        )
        # Jan 1 should be positive -- not ZERO.
        assert entry["jan1_balance"] > ZERO
        # Jan 1 should be less than the anchor balance (growth happened
        # between Jan and the anchor).
        assert entry["jan1_balance"] < Decimal("10000.00")

    def test_investment_pre_anchor_dec31_growth(
        self, app, db, seed_full_user_data,
    ):
        """Investment account with mid-year anchor projects Dec 31 correctly.

        The forward projection from the anchor should produce a Dec 31
        balance higher than the anchor balance, with non-zero growth
        covering the full year.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        inv_acct, _ = _create_investment_account(
            user, periods, employer_type="none",
        )
        inv_acct.current_anchor_period_id = periods[5].id
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "401k"
        )
        # Dec 31 should exceed the anchor balance from forward growth.
        assert entry["dec31_balance"] > Decimal("10000.00")
        # Full-year growth should be positive.
        assert entry["investment_growth"] > ZERO

    def test_investment_pre_anchor_employer(
        self, app, db, seed_full_user_data,
    ):
        """Investment account with employer match and mid-year anchor.

        Employer contributions should be non-zero and cover the full
        year (both pre-anchor and post-anchor periods).
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]
        salary_profile = data["salary_profile"]

        inv_acct, _ = _create_investment_account(
            user, periods,
            employer_type="match",
            match_pct=Decimal("0.5000"),
            match_cap_pct=Decimal("0.0600"),
        )

        flat_method = (
            db.session.query(CalcMethod)
            .filter_by(name="flat").one()
        )
        pre_tax_timing = (
            db.session.query(DeductionTiming)
            .filter_by(name="pre_tax").one()
        )
        ded = PaycheckDeduction(
            salary_profile_id=salary_profile.id,
            target_account_id=inv_acct.id,
            name="401k Contribution",
            amount=Decimal("200.00"),
            calc_method_id=flat_method.id,
            deduction_timing_id=pre_tax_timing.id,
            is_active=True,
        )
        db.session.add(ded)

        # Move anchor to period 5.
        inv_acct.current_anchor_period_id = periods[5].id
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "401k"
        )
        assert entry["employer_contributions"] > ZERO
        assert entry["dec31_balance"] > entry["jan1_balance"]
        assert entry["investment_growth"] > ZERO

    def test_ira_pre_anchor_not_zero(
        self, app, db, seed_full_user_data,
    ):
        """IRA with mid-year anchor shows non-zero balances and growth.

        IRAs typically have no paycheck deductions and no transfers.
        With anchor at period 5, the reverse projection should still
        derive a non-zero Jan 1 balance from the anchor balance, and
        the forward projection should show growth.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        # Create a Roth IRA with $5,000 balance.
        ira_type = (
            db.session.query(AccountType)
            .filter_by(name="Roth IRA").one()
        )
        ira_acct = Account(
            user_id=user.id,
            account_type_id=ira_type.id,
            name="Roth IRA",
            current_anchor_balance=Decimal("5000.00"),
            current_anchor_period_id=periods[5].id,
        )
        db.session.add(ira_acct)
        db.session.flush()

        ira_params = InvestmentParams(
            account_id=ira_acct.id,
            assumed_annual_return=Decimal("0.10500"),
        )
        db.session.add(ira_params)
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            (s for s in savings if s["account_name"] == "Roth IRA"),
            None,
        )
        assert entry is not None, "Roth IRA not in savings_progress"
        # Must not be zero -- this was the original bug.
        assert entry["jan1_balance"] > ZERO
        assert entry["dec31_balance"] > ZERO
        assert entry["investment_growth"] > ZERO

    def test_hysa_pre_anchor_jan1(
        self, app, db, seed_full_user_data,
    ):
        """HYSA with mid-year anchor has non-zero Jan 1 balance and interest.

        The anchor fallback should return the anchor balance for Jan 1,
        and the pre-anchor interest supplement should be included.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        hysa_acct = _create_hysa_account(user, periods)
        hysa_acct.current_anchor_period_id = periods[5].id
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            (s for s in savings
             if s["account_name"] == "High Yield Savings"),
            None,
        )
        assert entry is not None, "HYSA not in savings_progress"
        # Jan 1 should use the anchor balance fallback, not ZERO.
        assert entry["jan1_balance"] > ZERO
        # Interest should include pre-anchor periods.
        assert entry["investment_growth"] > ZERO

    def test_plain_savings_pre_anchor_jan1(
        self, app, db, seed_full_user_data,
    ):
        """Plain savings with mid-year anchor has non-zero Jan 1 balance.

        The anchor fallback should return the anchor balance for Jan 1.
        """
        data = seed_full_user_data
        savings_acct = data["savings_account"]
        user = data["user"]
        periods = data["periods"]

        # Move savings anchor to period 5.
        savings_acct.current_anchor_period_id = periods[5].id
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            (s for s in savings if s["account_name"] == "Savings"),
            None,
        )
        assert entry is not None, "Savings not in savings_progress"
        # Jan 1 should use the anchor balance fallback, not ZERO.
        assert entry["jan1_balance"] > ZERO

    def test_anchor_at_period_0_regression(
        self, app, db, seed_full_user_data,
    ):
        """Anchor at period 0 (no pre-anchor gap) works as before.

        Regression guard: the fix must not change behavior when the
        anchor is at the first period.
        """
        data = seed_full_user_data
        user = data["user"]
        periods = data["periods"]

        inv_acct, _ = _create_investment_account(
            user, periods, employer_type="none",
        )
        db.session.commit()

        result = compute_year_end_summary(user.id, YEAR)
        savings = result["savings_progress"]
        entry = next(
            s for s in savings if s["account_name"] == "401k"
        )
        # Same assertions as the existing test_savings_investment_with_growth.
        assert entry["investment_growth"] > ZERO
        assert entry["dec31_balance"] > entry["jan1_balance"]
        assert entry["employer_contributions"] == ZERO


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
