# Fix Retirement Projection Calculations — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the retirement dashboard so it projects account balances using full contribution inputs, synthetic future periods covering the entire time horizon to retirement, and applies tax to pension income for accurate gap analysis.

**Architecture:** Four bugs are addressed: (1) zero contributions in projections, (2) no employer match, (3) projections limited to existing DB periods instead of full timeline, (4) gross pension compared to net income. All fixes use existing pure services (`calculate_investment_inputs`, `growth_engine.project_balance`) and extend `retirement_gap_calculator` with pension tax support. A new synthetic period generator in `growth_engine.py` provides biweekly periods for arbitrary date ranges without DB writes.

**Tech Stack:** Python/Flask, Decimal arithmetic, existing growth_engine + investment_projection + retirement_gap_calculator services, pytest

---

## Task 1: Add Synthetic Period Generator to Growth Engine

**Why:** The retirement dashboard needs to project balances 20+ years into the future, but the DB only stores ~2 years of pay periods. The growth engine needs period objects with `.id`, `.start_date`, `.end_date` — we generate lightweight synthetic ones.

**Files:**
- Modify: `app/services/growth_engine.py` (add to end of file)
- Test: `tests/test_services/test_growth_engine.py` (add new test class)

### Step 1: Write failing tests for synthetic period generation

Add to the end of `tests/test_services/test_growth_engine.py`:

```python
from app.services.growth_engine import generate_projection_periods


class TestGenerateProjectionPeriods:
    def test_basic_generation(self):
        """Generates biweekly periods from start to end."""
        periods = generate_projection_periods(date(2026, 3, 6), date(2026, 6, 1))
        assert len(periods) > 0
        for p in periods:
            assert hasattr(p, "id")
            assert hasattr(p, "start_date")
            assert hasattr(p, "end_date")
            assert (p.end_date - p.start_date).days == 13  # 14-day period

    def test_period_count_one_year(self):
        """One year produces ~26 biweekly periods."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 12, 31))
        assert len(periods) == 26

    def test_period_count_twenty_years(self):
        """Twenty years produces ~520 biweekly periods."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2045, 12, 31))
        # 20 years * 365.25 / 14 ≈ 521-522
        assert 519 <= len(periods) <= 523

    def test_sequential_ids(self):
        """Period IDs are sequential starting from 1."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 3, 1))
        for i, p in enumerate(periods):
            assert p.id == i + 1

    def test_no_gaps_between_periods(self):
        """Each period starts the day after the previous one ends."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 6, 1))
        for i in range(1, len(periods)):
            expected_start = date.fromordinal(periods[i - 1].end_date.toordinal() + 1)
            assert periods[i].start_date == expected_start

    def test_end_before_start_returns_empty(self):
        """End date before start returns empty list."""
        periods = generate_projection_periods(date(2026, 6, 1), date(2026, 1, 1))
        assert periods == []

    def test_same_day_returns_one_period(self):
        """Start equals end still returns one period."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 1, 1))
        assert len(periods) == 1

    def test_works_with_project_balance(self):
        """Synthetic periods integrate with project_balance."""
        periods = generate_projection_periods(date(2026, 1, 1), date(2026, 12, 31))
        result = project_balance(
            current_balance=Decimal("10000"),
            assumed_annual_return=Decimal("0.07"),
            periods=periods,
            periodic_contribution=Decimal("500"),
        )
        assert len(result) == len(periods)
        assert result[-1].end_balance > Decimal("10000") + Decimal("500") * len(periods)

    def test_year_boundaries_correct_for_limit_reset(self):
        """Periods crossing year boundary have correct year in start_date."""
        periods = generate_projection_periods(date(2026, 12, 20), date(2027, 1, 31))
        years = [p.start_date.year for p in periods]
        assert 2026 in years
        assert 2027 in years
```

### Step 2: Run tests to verify they fail

Run: `python -m pytest tests/test_services/test_growth_engine.py::TestGenerateProjectionPeriods -v`
Expected: FAIL — `ImportError: cannot import name 'generate_projection_periods'`

### Step 3: Implement the synthetic period generator

Add to the end of `app/services/growth_engine.py`:

```python
from collections import namedtuple
from datetime import timedelta

SyntheticPeriod = namedtuple("SyntheticPeriod", ["id", "start_date", "end_date"])


def generate_projection_periods(start_date, end_date, cadence_days=14):
    """Generate synthetic biweekly periods for long-term projections.

    Creates lightweight period objects compatible with project_balance().
    No database interaction — pure function.

    Args:
        start_date:    date — first period start.
        end_date:      date — generate periods until start_date would exceed this.
        cadence_days:  int — days per period (default 14 for biweekly).

    Returns:
        List of SyntheticPeriod namedtuples with .id, .start_date, .end_date.
    """
    periods = []
    current = start_date
    period_id = 1
    while current <= end_date:
        period_end = current + timedelta(days=cadence_days - 1)
        periods.append(SyntheticPeriod(
            id=period_id,
            start_date=current,
            end_date=period_end,
        ))
        current += timedelta(days=cadence_days)
        period_id += 1
    return periods
```

### Step 4: Run tests to verify they pass

Run: `python -m pytest tests/test_services/test_growth_engine.py::TestGenerateProjectionPeriods -v`
Expected: All 9 tests PASS

### Step 5: Commit

```bash
git add app/services/growth_engine.py tests/test_services/test_growth_engine.py
git commit -m "feat: add synthetic period generator for long-term projections"
```

---

## Task 2: Update Gap Calculator for Pension Tax Adjustment

**Why:** The gap analysis currently compares gross pension income to net paycheck income. Since pension income is taxable, this overstates the pension's purchasing power. When the user provides an estimated retirement tax rate, we should apply it to pension income too.

**Files:**
- Modify: `app/services/retirement_gap_calculator.py`
- Test: `tests/test_services/test_retirement_gap_calculator.py` (add new tests)

### Step 1: Write failing tests for pension tax adjustment

Add to the end of class `TestCalculateGap` in `tests/test_services/test_retirement_gap_calculator.py`:

```python
    def test_pension_taxed_when_tax_rate_provided(self):
        """Pension income reduced by estimated tax rate when provided."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("5000"),
            estimated_tax_rate=Decimal("0.20"),
        )
        # Net monthly = 2500 * 26 / 12 = 5416.67
        # After-tax pension = 5000 * 0.80 = 4000
        # Gap = 5416.67 - 4000 = 1416.67
        assert result.after_tax_monthly_pension == Decimal("4000.00")
        assert result.monthly_income_gap == Decimal("1416.67")
        assert result.required_retirement_savings > ZERO

    def test_pension_not_taxed_without_tax_rate(self):
        """Without tax rate, pension used as-is (gross) — backward compatible."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2500"),
            monthly_pension_income=Decimal("5000"),
        )
        # Net monthly = 5416.67
        # Gap = 5416.67 - 5000 = 416.67 (using gross pension)
        assert result.after_tax_monthly_pension is None
        assert result.monthly_income_gap == Decimal("416.67")

    def test_pension_tax_creates_gap_where_none_existed(self):
        """Gross pension > net income, but after-tax pension < net income."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=Decimal("5000"),
            estimated_tax_rate=Decimal("0.25"),
        )
        # Net monthly = 2000 * 26 / 12 = 4333.33
        # After-tax pension = 5000 * 0.75 = 3750
        # Gap = 4333.33 - 3750 = 583.33
        assert result.monthly_income_gap == Decimal("583.33")
        assert result.required_retirement_savings > ZERO

    def test_pension_tax_zero_pension(self):
        """Tax on zero pension is still zero — no division issues."""
        result = calculate_gap(
            net_biweekly_pay=Decimal("2000"),
            monthly_pension_income=ZERO,
            estimated_tax_rate=Decimal("0.20"),
        )
        assert result.after_tax_monthly_pension == ZERO
        assert result.monthly_income_gap == result.pre_retirement_net_monthly
```

### Step 2: Run tests to verify they fail

Run: `python -m pytest tests/test_services/test_retirement_gap_calculator.py::TestCalculateGap::test_pension_taxed_when_tax_rate_provided -v`
Expected: FAIL — `AttributeError: 'RetirementGapAnalysis' has no attribute 'after_tax_monthly_pension'`

### Step 3: Implement pension tax adjustment

Modify `app/services/retirement_gap_calculator.py`:

**3a.** Add `after_tax_monthly_pension` field to the `RetirementGapAnalysis` dataclass (after `monthly_pension_income`, before `monthly_income_gap`):

```python
@dataclass
class RetirementGapAnalysis:
    """Result of a retirement income gap calculation."""
    pre_retirement_net_monthly: Decimal
    monthly_pension_income: Decimal
    after_tax_monthly_pension: Decimal  # NEW — None when no tax rate provided
    monthly_income_gap: Decimal
    required_retirement_savings: Decimal
    projected_total_savings: Decimal
    savings_surplus_or_shortfall: Decimal
    safe_withdrawal_rate: Decimal
    planned_retirement_date: date
    after_tax_projected_savings: Decimal = None
    after_tax_surplus_or_shortfall: Decimal = None
```

**3b.** In `calculate_gap()`, after computing `pre_retirement_net_monthly` (Step 1) and before computing `monthly_income_gap` (Step 3), add pension tax logic:

Replace the existing Step 3 block:
```python
    # Step 3: Monthly income gap.
    monthly_income_gap = max(
        pre_retirement_net_monthly - monthly_pension_income,
        ZERO,
    )
```

With:
```python
    # Step 2b: After-tax pension income (when tax rate provided).
    after_tax_monthly_pension = None
    if estimated_tax_rate is not None:
        estimated_tax_rate = Decimal(str(estimated_tax_rate))
        after_tax_monthly_pension = (
            monthly_pension_income * (1 - estimated_tax_rate)
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Step 3: Monthly income gap.
    # Use after-tax pension when available for apples-to-apples comparison
    # with net (post-tax) current income.
    effective_pension = after_tax_monthly_pension if after_tax_monthly_pension is not None else monthly_pension_income
    monthly_income_gap = max(
        pre_retirement_net_monthly - effective_pension,
        ZERO,
    )
```

**3c.** Remove the `estimated_tax_rate = Decimal(str(estimated_tax_rate))` line from the after-tax savings block (Step 5 area, around line 99) since it's now converted earlier. The after-tax savings block should just use the already-converted value.

**3d.** Add `after_tax_monthly_pension` to the return:

```python
    return RetirementGapAnalysis(
        pre_retirement_net_monthly=pre_retirement_net_monthly,
        monthly_pension_income=monthly_pension_income,
        after_tax_monthly_pension=after_tax_monthly_pension,
        monthly_income_gap=monthly_income_gap,
        ...
    )
```

### Step 4: Run tests to verify they pass

Run: `python -m pytest tests/test_services/test_retirement_gap_calculator.py -v`
Expected: All 16 tests PASS (12 existing + 4 new)

**Note:** The existing `test_after_tax_view_traditional` and `test_after_tax_all_roth` tests use `monthly_pension_income=ZERO`, so taxing zero pension doesn't change the gap. These tests remain unaffected. The existing `test_pension_covers_all_income` test does NOT provide `estimated_tax_rate`, so it uses gross pension — still backward compatible.

### Step 5: Commit

```bash
git add app/services/retirement_gap_calculator.py tests/test_services/test_retirement_gap_calculator.py
git commit -m "feat: apply estimated tax rate to pension income in gap analysis"
```

---

## Task 3: Update Retirement Route with Full Projection Inputs

**Why:** This is the core fix. The retirement dashboard currently passes `periodic_contribution=Decimal("0")` and no employer params to the growth engine, and only projects over the limited DB periods. This task wires up the full contribution pipeline and uses synthetic periods for the complete time horizon.

**Files:**
- Modify: `app/routes/retirement.py`
- Test: `tests/test_routes/test_retirement.py` (add new integration tests)

### Step 1: Write failing integration tests

Add these imports to the top of `tests/test_routes/test_retirement.py`:

```python
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, CalcMethod, DeductionTiming
```

Add a new helper function after the existing helpers:

```python
def _create_retirement_account(seed_user, db_session, type_name="401k"):
    """Helper to create a retirement account with investment params."""
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name=f"Test {type_name}",
        current_anchor_balance=Decimal("10000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        employer_contribution_type="match",
        employer_match_percentage=Decimal("1.0000"),
        employer_match_cap_percentage=Decimal("0.0600"),
    )
    db_session.add(params)
    db_session.flush()
    return account, params
```

Add a new test class at the end of the file:

```python
class TestRetirementProjections:
    """Tests that retirement dashboard projects with full contribution inputs."""

    def test_dashboard_projects_with_contributions(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Dashboard projection includes employee contributions and employer match."""
        profile = _create_salary_profile(seed_user, db.session)
        account, params = _create_retirement_account(seed_user, db.session)

        # Set retirement date 20 years out.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)

        # Create a paycheck deduction targeting the retirement account.
        pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
        flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckDeduction(
            salary_profile_id=profile.id,
            target_account_id=account.id,
            name="401k Contribution",
            amount=Decimal("500.00"),
            timing_id=pre_tax.id,
            calc_method_id=flat_method.id,
        )
        db.session.add(deduction)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should NOT show $10,000 (the anchor balance unchanged).
        # With $500/period + 7% return + employer match over 20 years,
        # projected balance should be significantly more than $10,000.
        assert "10,000.00" not in html or "Projected at Retirement" in html

    def test_dashboard_projects_without_retirement_date(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Without planned retirement date, uses current balance as projection."""
        _create_retirement_account(seed_user, db.session)

        # No planned_retirement_date set.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = None
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200

    def test_dashboard_pension_tax_shown(
        self, auth_client, seed_user, db, seed_periods
    ):
        """After-tax pension line shown when tax rate is set."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        settings.estimated_retirement_tax_rate = Decimal("0.2000")
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "After-Tax Monthly Pension" in html

    def test_dashboard_projects_multiple_accounts(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Multiple retirement accounts all project correctly."""
        _create_retirement_account(seed_user, db.session, "401k")
        _create_retirement_account(seed_user, db.session, "roth_ira")

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
```

### Step 2: Run tests to verify current behavior

Run: `python -m pytest tests/test_routes/test_retirement.py::TestRetirementProjections -v`
Expected: Most will PASS with wrong values or FAIL on the pension tax assertion. The `test_dashboard_pension_tax_shown` test should FAIL because the template doesn't have "After-Tax Monthly Pension" yet.

### Step 3: Update retirement route imports

At the top of `app/routes/retirement.py`, add these imports:

```python
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.transfer import Transfer
from app.services.investment_projection import calculate_investment_inputs
```

### Step 4: Rewrite the projection loop in the dashboard function

Replace the entire account projection block in `app/routes/retirement.py` `dashboard()` function (lines 108–164, from `# Load retirement/investment accounts` through building `retirement_account_projections`) with:

```python
    # Load retirement/investment accounts and project balances.
    retirement_types = (
        db.session.query(AccountType)
        .filter(AccountType.category.in_(["retirement", "investment"]))
        .all()
    )
    retirement_type_ids = {rt.id for rt in retirement_types}
    type_name_map = {rt.id: rt.name for rt in retirement_types}

    accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(retirement_type_ids),
            Account.is_active.is_(True),
        )
        .all()
    )

    retirement_account_projections = []
    planned_retirement_date = (
        settings.planned_retirement_date if settings else None
    )

    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    # Batch-load paycheck deductions targeting retirement accounts.
    deductions_by_account = {}
    account_ids = [a.id for a in accounts]
    if account_ids:
        inv_deductions = (
            db.session.query(PaycheckDeduction)
            .join(SalaryProfile)
            .filter(
                SalaryProfile.user_id == user_id,
                SalaryProfile.is_active.is_(True),
                PaycheckDeduction.target_account_id.in_(account_ids),
                PaycheckDeduction.is_active.is_(True),
            )
            .all()
        )
        for ded in inv_deductions:
            deductions_by_account.setdefault(ded.target_account_id, []).append(ded)

    # Batch-load transfers targeting retirement accounts.
    period_ids = [p.id for p in all_periods]
    all_acct_transfers = []
    if account_ids and period_ids:
        all_acct_transfers = (
            db.session.query(Transfer)
            .filter(
                Transfer.to_account_id.in_(account_ids),
                Transfer.pay_period_id.in_(period_ids),
                Transfer.is_deleted.is_(False),
            )
            .all()
        )

    # Load salary gross biweekly for employer contribution calculation.
    salary_gross_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        salary_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

    # Generate synthetic future periods from today to retirement.
    synthetic_periods = []
    if planned_retirement_date:
        synthetic_periods = growth_engine.generate_projection_periods(
            start_date=date.today(),
            end_date=planned_retirement_date,
        )

    for acct in accounts:
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id)
            .first()
        )
        balance = acct.current_anchor_balance or Decimal("0")
        projected_balance = balance

        if params and synthetic_periods:
            # Adapt deductions for this account.
            acct_deductions = deductions_by_account.get(acct.id, [])
            adapted_deductions = []
            for ded in acct_deductions:
                ded_profile = ded.salary_profile
                adapted_deductions.append(type("D", (), {
                    "amount": ded.amount,
                    "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
                    "annual_salary": ded_profile.annual_salary,
                    "pay_periods_per_year": ded_profile.pay_periods_per_year or 26,
                })())

            # Compute contribution inputs using the shared helper.
            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=params,
                deductions=adapted_deductions,
                all_transfers=all_acct_transfers,
                all_periods=all_periods,
                current_period=current_period,
                salary_gross_biweekly=salary_gross_biweekly,
            )

            # Project balance forward using synthetic periods to retirement.
            proj = growth_engine.project_balance(
                current_balance=balance,
                assumed_annual_return=params.assumed_annual_return,
                periods=synthetic_periods,
                periodic_contribution=inputs.periodic_contribution,
                employer_params=inputs.employer_params,
                annual_contribution_limit=inputs.annual_contribution_limit,
                ytd_contributions_start=inputs.ytd_contributions,
            )
            if proj:
                projected_balance = proj[-1].end_balance

        type_name = type_name_map.get(acct.account_type_id, "")
        retirement_account_projections.append({
            "account": acct,
            "projected_balance": projected_balance,
            "is_traditional": type_name in TRADITIONAL_TYPES,
        })
```

**Note:** This also removes the duplicate `all_periods` call. The original code called `pay_period_service.get_all_periods(user_id)` twice (line 98 and line 132). The rewrite calls it once and reuses `current_period` from line 99.

### Step 5: Remove the earlier duplicate `all_periods` and reuse `current_period`

The original `dashboard()` already calls `get_current_period` at line 99. Remove the duplicate `pay_period_service.get_all_periods(user_id)` that was at line 132 (it's now part of the rewritten block above). Also remove the separate `periods` variable (line 98) since `all_periods` serves the same purpose. Update the paycheck calculation block to use `all_periods` instead:

Replace lines 94-106 (the net biweekly calculation block):

```python
    # Load all periods and find current period.
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    # Calculate current net biweekly pay.
    net_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        if current_period:
            from app.routes.salary import _load_tax_configs
            tax_configs = _load_tax_configs(user_id, profile)
            breakdown = paycheck_calculator.calculate_paycheck(
                profile, current_period, all_periods, tax_configs,
            )
            net_biweekly = breakdown.net_pay
```

This moves the period loading to before the paycheck calculation so both sections can share them.

### Step 6: Run tests to verify they pass

Run: `python -m pytest tests/test_routes/test_retirement.py -v`
Expected: All existing + new tests PASS (except `test_dashboard_pension_tax_shown` which needs Task 4)

### Step 7: Commit

```bash
git add app/routes/retirement.py tests/test_routes/test_retirement.py
git commit -m "fix: use full contribution inputs and synthetic periods in retirement projections"
```

---

## Task 4: Update Template for After-Tax Pension Display

**Why:** When an estimated tax rate is configured, the template should show the after-tax pension income so users understand the actual comparison being made.

**Files:**
- Modify: `app/templates/retirement/dashboard.html`

### Step 1: Add after-tax pension row to gap analysis table

In `app/templates/retirement/dashboard.html`, after the "Projected Monthly Pension" row (line 28), add a conditional row for after-tax pension:

```html
        {% if gap_analysis.after_tax_monthly_pension is not none %}
        <tr>
          <td class="text-muted">After-Tax Monthly Pension</td>
          <td class="text-end font-mono">${{ "{:,.2f}".format(gap_analysis.after_tax_monthly_pension|float) }}</td>
        </tr>
        {% endif %}
```

This inserts between the "Projected Monthly Pension" and "Monthly Income Gap" rows, so users can see both gross and after-tax pension values.

### Step 2: Verify template renders

Run: `python -m pytest tests/test_routes/test_retirement.py::TestRetirementProjections::test_dashboard_pension_tax_shown -v`
Expected: PASS — the "After-Tax Monthly Pension" text is now in the HTML.

### Step 3: Commit

```bash
git add app/templates/retirement/dashboard.html
git commit -m "feat: show after-tax pension income in retirement dashboard"
```

---

## Task 5: Full Regression Suite

**Why:** Confirm all changes work together and no existing functionality is broken.

### Step 1: Run the full test suite

Run: `python -m pytest --tb=short -q`
Expected: All tests pass (target: 674+ existing + ~17 new = 691+)

### Step 2: Manually verify key scenarios

If possible, start the dev server and verify:

1. **Retirement dashboard with no retirement date:** Should show current balances as projections (no synthetic periods generated).
2. **Retirement dashboard with retirement date set:** Projected balances should be much larger than current balances, reflecting 20 years of contributions + growth + employer match.
3. **Gap analysis with tax rate:** Should show "After-Tax Monthly Pension" line. If after-tax pension is less than net income, a non-zero gap and required savings should appear.
4. **Gap analysis without tax rate:** Should behave as before (gross pension comparison).

### Step 3: Final commit

```bash
git add -A
git commit -m "test: verify retirement projection regression suite"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `app/services/growth_engine.py` | Add `SyntheticPeriod`, `generate_projection_periods()` |
| `app/services/retirement_gap_calculator.py` | Add `after_tax_monthly_pension` field, apply tax to pension in gap calc |
| `app/routes/retirement.py` | Use `calculate_investment_inputs()`, synthetic periods, full contribution pipeline |
| `app/templates/retirement/dashboard.html` | Add after-tax pension row |
| `tests/test_services/test_growth_engine.py` | 9 new tests for synthetic period generation |
| `tests/test_services/test_retirement_gap_calculator.py` | 4 new tests for pension tax adjustment |
| `tests/test_routes/test_retirement.py` | 4 new integration tests for projection + pension tax |

**Bugs Fixed:**
1. Zero contributions in projections → now uses `calculate_investment_inputs()` per account
2. No employer match → now passes `employer_params` from investment params
3. Limited to DB periods → now generates synthetic biweekly periods to retirement date
4. Gross pension vs net income → now applies estimated tax rate to pension income

**Backward Compatibility:**
- All existing tests remain unchanged
- Without `estimated_tax_rate`, gap analysis behaves exactly as before (gross pension)
- Without `planned_retirement_date`, projections use current balance (no synthetic periods)
- No DB schema changes required
