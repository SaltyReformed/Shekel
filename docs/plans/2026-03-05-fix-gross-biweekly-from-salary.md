# Fix gross_biweekly Sourced from Salary Profile

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure employer contributions (flat_percentage and match) use the user's actual gross salary per period, sourced from the active salary profile — not derived as a side effect of deductions targeting the account.

**Architecture:** Add an optional `salary_gross_biweekly` parameter to `calculate_investment_inputs()`. When provided, use it as the `gross_biweekly` for employer params instead of only deriving it from deductions. Both callers (savings.py and investment.py) load the active salary profile and pass it in.

**Tech Stack:** Flask, SQLAlchemy, pytest

---

## Root Cause

`investment_projection.py:55` initializes `gross_biweekly = ZERO` and only updates it inside the deduction loop (line 61). If no `PaycheckDeduction` records have `target_account_id` pointing to the investment account, the loop is empty and `gross_biweekly` stays 0. This makes employer contributions `0 * pct = 0` for both `flat_percentage` and `match` types.

`gross_biweekly` is fundamentally a property of the salary profile, not of deductions.

---

### Task 1: Add failing tests for salary-sourced gross_biweekly

**Files:**
- Modify: `tests/test_services/test_investment_projection.py`

**Step 1: Write failing tests**

Add these two tests to `TestCalculateInvestmentInputs`:

```python
    def test_employer_flat_uses_salary_gross_when_no_deductions(self):
        """Employer flat_percentage works even without deductions targeting the account."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="flat_percentage",
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        # No deductions, but salary_gross_biweekly provided.
        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=[],
            all_transfers=[],
            all_periods=[current_period],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),
        )

        assert result.employer_params is not None
        assert result.employer_params["gross_biweekly"] == Decimal("3846.15")
        assert result.periodic_contribution == Decimal("0")

    def test_deduction_gross_overrides_salary_gross(self):
        """When deductions exist, their derived gross takes precedence."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="flat_percentage",
            employer_flat_percentage=Decimal("0.05"),
        )
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"),
                calc_method_name="flat",
                annual_salary=Decimal("120000"),
                pay_periods_per_year=26,
            ),
        ]
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=deductions,
            all_transfers=[],
            all_periods=[current_period],
            current_period=current_period,
            salary_gross_biweekly=Decimal("3846.15"),  # from $100k — should be overridden
        )

        # Deduction has $120k salary → gross = 120000/26 = $4615.38
        expected_gross = (Decimal("120000") / 26).quantize(Decimal("0.01"))
        assert result.employer_params["gross_biweekly"] == expected_gross
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_services/test_investment_projection.py::TestCalculateInvestmentInputs::test_employer_flat_uses_salary_gross_when_no_deductions tests/test_services/test_investment_projection.py::TestCalculateInvestmentInputs::test_deduction_gross_overrides_salary_gross -v`

Expected: FAIL — `calculate_investment_inputs()` doesn't accept `salary_gross_biweekly` yet.

**Step 3: Commit the failing tests**

```bash
git add tests/test_services/test_investment_projection.py
git commit -m "test: add failing tests for salary-sourced gross_biweekly"
```

---

### Task 2: Add salary_gross_biweekly parameter to shared helper

**Files:**
- Modify: `app/services/investment_projection.py`

**Step 1: Update the function signature and logic**

Change the function signature to accept the new parameter:

```python
def calculate_investment_inputs(
    account_id,
    investment_params,
    deductions,
    all_transfers,
    all_periods,
    current_period,
    salary_gross_biweekly=None,
):
```

Update the docstring Args to add:

```
        salary_gross_biweekly: Optional Decimal — gross pay per period from the
                            user's salary profile. Used as fallback for employer
                            contribution calculation when no deductions target
                            the account.
```

After the deduction loop (after line 65), add a fallback:

```python
    # Use salary profile gross as fallback when no deductions provided one.
    if gross_biweekly == ZERO and salary_gross_biweekly is not None:
        gross_biweekly = Decimal(str(salary_gross_biweekly))
```

**Step 2: Run the new tests**

Run: `pytest tests/test_services/test_investment_projection.py -v`

Expected: All 11 tests PASS (9 existing + 2 new).

**Step 3: Run existing tests to verify no regressions**

Existing tests don't pass `salary_gross_biweekly` (it defaults to None), so they should all still pass with their current behavior — deduction-derived gross is used when deductions exist, and the fallback is never triggered.

**Step 4: Commit**

```bash
git add app/services/investment_projection.py tests/test_services/test_investment_projection.py
git commit -m "feat: add salary_gross_biweekly fallback to investment projection helper"
```

---

### Task 3: Wire up salary profile in both callers

**Files:**
- Modify: `app/routes/savings.py`
- Modify: `app/routes/investment.py`

**Step 1: Write a failing route test**

Add this test to `TestDashboard` in `tests/test_routes/test_savings.py`:

```python
    def test_dashboard_employer_contribution_without_employee_deduction(
        self, app, auth_client, seed_user,
    ):
        """Employer flat 5% works even when no paycheck deduction targets the account."""
        import re
        from app.services import pay_period_service

        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=40,
                cadence_days=14,
            )
            db.session.flush()

            # Create 401k with employer flat 5% but NO employee deduction.
            acct_type = db.session.query(AccountType).filter_by(name="401k").one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=acct_type.id,
                name="Employer Only 401k",
                current_anchor_balance=Decimal("50000.00"),
                current_anchor_period_id=periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()

            params = InvestmentParams(
                account_id=acct.id,
                assumed_annual_return=Decimal("0.07000"),
                annual_contribution_limit=Decimal("23500.00"),
                contribution_limit_year=2026,
                employer_contribution_type="flat_percentage",
                employer_flat_percentage=Decimal("0.0500"),
            )
            db.session.add(params)

            # Create salary profile (no deduction targeting the 401k).
            filing_status = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing_status.id,
                name="Main Job",
                annual_salary=Decimal("100000.00"),
                pay_periods_per_year=26,
                state_code="NC",
            )
            db.session.add(profile)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With 5% employer on $3846/period (~$5k/yr) + 7% growth on $50k,
            # 1-year should be ~$58k+. Without employer, growth-only ~$53.5k.
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [
                int(a.replace(',', ''))
                for a in amounts
                if int(a.replace(',', '')) > 54000
            ]

            assert len(amounts_int) > 0, (
                "Expected projected amount > $54,000 with employer 5% flat contribution, "
                f"but found amounts: {amounts}. Employer contribution not applied."
            )
```

**Step 2: Run the test to verify it fails**

Run: `pytest tests/test_routes/test_savings.py::TestDashboard::test_dashboard_employer_contribution_without_employee_deduction -v`

Expected: FAIL — `salary_gross_biweekly` is not yet passed by the caller.

**Step 3: Update savings.py**

Add this query **before** the account loop (after the `deductions_by_account` block, before `loan_params_map`):

```python
    # Load active salary profile for employer contribution gross calculation.
    salary_gross_biweekly = Decimal("0")
    active_profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if active_profile:
        salary_gross_biweekly = (
            Decimal(str(active_profile.annual_salary))
            / (active_profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))
```

Then in the `elif acct_investment_params and current_period:` block, pass it to the helper:

```python
            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=acct_investment_params,
                deductions=adapted_deductions,
                all_transfers=all_transfers,
                all_periods=all_periods,
                current_period=current_period,
                salary_gross_biweekly=salary_gross_biweekly,
            )
```

**Step 4: Update investment.py**

Add the same salary profile lookup. After loading `current_balance` (~line 59), add:

```python
    # Load active salary profile for employer contribution gross calculation.
    salary_gross_biweekly = Decimal("0")
    active_profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if active_profile:
        salary_gross_biweekly = (
            Decimal(str(active_profile.annual_salary))
            / (active_profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))
```

Then pass it to the helper call:

```python
    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=params,
        deductions=adapted_deductions,
        all_transfers=acct_transfers,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )
```

**Step 5: Run the new test**

Run: `pytest tests/test_routes/test_savings.py::TestDashboard::test_dashboard_employer_contribution_without_employee_deduction -v`

Expected: PASS.

**Step 6: Run both full route suites**

Run: `pytest tests/test_routes/test_savings.py tests/test_routes/test_investment.py -v`

Expected: All pass.

**Step 7: Commit**

```bash
git add app/routes/savings.py app/routes/investment.py tests/test_routes/test_savings.py
git commit -m "fix: source gross_biweekly from salary profile for employer contributions"
```

---

### Task 4: Full regression suite

**Files:** None (verification only)

**Step 1: Run all tests**

Run: `pytest --tb=short -q`

Expected: All tests pass (703 baseline + 3 new = ~706), 0 failures.
