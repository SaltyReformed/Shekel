# Full Investment Projections on Savings Dashboard

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make savings dashboard cards for investment/retirement accounts project balances using ALL inputs: compound growth, employee contributions (paycheck deductions), employer contributions, transfer-based contributions, annual contribution limits, and YTD contributions — matching the investment detail page exactly.

**Architecture:** Extract the contribution calculation logic (paycheck deductions, transfer averages, employer params, YTD) into a shared helper function in `app/services/investment_projection.py`. Both the investment detail route and the savings dashboard call it, eliminating duplication. The savings dashboard batch-loads PaycheckDeductions for all investment accounts in one query, then passes per-account data through the shared helper.

**Tech Stack:** Flask, SQLAlchemy, growth_engine.py (pure functions), pytest

---

## Gap Analysis

The investment detail page (`investment.py:60-146`) computes 6 inputs for `growth_engine.project_balance()`. The savings dashboard currently only passes 2 of them:

| Input | Investment Detail | Savings Dashboard | Status |
|---|---|---|---|
| `current_balance` | anchor balance | anchor balance | OK |
| `assumed_annual_return` | from InvestmentParams | from InvestmentParams | OK |
| `periodic_contribution` | paycheck deductions + transfer avg | **not passed** | MISSING |
| `employer_params` | from InvestmentParams + gross salary | **not passed** | MISSING |
| `annual_contribution_limit` | from InvestmentParams | **not passed** | MISSING |
| `ytd_contributions_start` | from transfers in current year | **not passed** | MISSING |

### Data Sources for Missing Inputs

1. **`periodic_contribution`** — Sum of:
   - PaycheckDeduction records where `target_account_id == account.id`, joined to active SalaryProfiles. Percentage-based deductions use `gross_biweekly = annual_salary / pay_periods_per_year`.
   - Average per-period Transfer amount into the account (from `all_transfers`, already loaded).

2. **`employer_params`** — Built from `InvestmentParams` fields (`employer_contribution_type`, `employer_flat_percentage`, `employer_match_percentage`, `employer_match_cap_percentage`) plus `gross_biweekly` from the SalaryProfile.

3. **`annual_contribution_limit`** — Direct from `InvestmentParams.annual_contribution_limit` (already loaded).

4. **`ytd_contributions_start`** — Sum of Transfer amounts into the account for periods in the current year up to the current period. Can compute from `all_transfers` (already loaded).

### Key Models

- `PaycheckDeduction` (`salary.paycheck_deductions`): `target_account_id`, `amount`, `calc_method` (flat/percentage), `salary_profile_id`
- `SalaryProfile` (`salary.salary_profiles`): `annual_salary`, `pay_periods_per_year`, `user_id`, `is_active`
- `InvestmentParams` (`budget.investment_params`): employer fields, `assumed_annual_return`, `annual_contribution_limit`
- `Transfer` (`budget.transfers`): `to_account_id`, `amount`, `pay_period_id`

---

### Task 1: Create shared investment projection helper

**Files:**
- Create: `app/services/investment_projection.py`
- Test: `tests/test_services/test_investment_projection.py`

This helper encapsulates the contribution calculation logic currently duplicated (partially) between `investment.py` and `savings.py`.

**Step 1: Write failing tests**

Create `tests/test_services/test_investment_projection.py`:

```python
"""
Tests for the investment projection helper.

Verifies that periodic contribution, employer params, and YTD
contributions are correctly computed from deductions, transfers,
and investment params.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from app.services.investment_projection import (
    calculate_investment_inputs,
    InvestmentInputs,
)


# ---- Fakes ----------------------------------------------------------------

@dataclass
class FakeDeduction:
    amount: Decimal
    calc_method_name: str  # "flat" or "percentage"
    annual_salary: Decimal
    pay_periods_per_year: int


@dataclass
class FakeTransfer:
    to_account_id: int
    amount: Decimal
    pay_period_id: int
    is_deleted: bool = False


@dataclass
class FakePeriod:
    id: int
    start_date: date
    period_index: int


@dataclass
class FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type: str
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


# ---- Tests ----------------------------------------------------------------

class TestCalculateInvestmentInputs:
    """Tests for calculate_investment_inputs()."""

    def test_no_deductions_no_transfers(self):
        """With no contributions, all amounts are zero."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=[],
            all_transfers=[],
            all_periods=[current_period],
            current_period=current_period,
        )

        assert result.periodic_contribution == Decimal("0")
        assert result.employer_params is None
        assert result.ytd_contributions == Decimal("0")
        assert result.annual_contribution_limit == Decimal("23500")

    def test_flat_deduction(self):
        """Flat dollar deduction contributes directly."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"),
                calc_method_name="flat",
                annual_salary=Decimal("100000"),
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
        )

        assert result.periodic_contribution == Decimal("500.00")

    def test_percentage_deduction(self):
        """Percentage deduction uses gross_biweekly = salary / periods_per_year."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        # 7% of $100,000/26 = 7% of $3846.15 = $269.23
        deductions = [
            FakeDeduction(
                amount=Decimal("0.07"),
                calc_method_name="percentage",
                annual_salary=Decimal("100000"),
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
        )

        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        expected = (gross * Decimal("0.07")).quantize(Decimal("0.01"))
        assert result.periodic_contribution == expected

    def test_transfer_contributions_averaged(self):
        """Transfer amounts are averaged across periods with transfers."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=None,
            employer_contribution_type="none",
        )
        transfers = [
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=1),
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=2),
            FakeTransfer(to_account_id=10, amount=Decimal("300"), pay_period_id=3),
            # Transfer to different account — should be ignored.
            FakeTransfer(to_account_id=99, amount=Decimal("1000"), pay_period_id=1),
        ]
        periods = [
            FakePeriod(id=i, start_date=date(2026, 1, 2), period_index=i)
            for i in range(1, 4)
        ]
        current_period = periods[0]

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=[],
            all_transfers=transfers,
            all_periods=periods,
            current_period=current_period,
        )

        # $700 across 3 periods = $233.33
        assert result.periodic_contribution == Decimal("233.33")

    def test_employer_flat_percentage(self):
        """Flat percentage employer contribution builds correct params."""
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
                annual_salary=Decimal("100000"),
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
        )

        assert result.employer_params is not None
        assert result.employer_params["type"] == "flat_percentage"
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        gross = (Decimal("100000") / 26).quantize(Decimal("0.01"))
        assert result.employer_params["gross_biweekly"] == gross

    def test_employer_match(self):
        """Match employer contribution builds correct params."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="match",
            employer_match_percentage=Decimal("1.0"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"),
                calc_method_name="flat",
                annual_salary=Decimal("100000"),
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
        )

        assert result.employer_params is not None
        assert result.employer_params["type"] == "match"
        assert result.employer_params["match_percentage"] == Decimal("1.0")
        assert result.employer_params["match_cap_percentage"] == Decimal("0.06")

    def test_ytd_contributions_from_transfers(self):
        """YTD sums transfers for current year up to current period."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        periods = [
            FakePeriod(id=1, start_date=date(2025, 12, 19), period_index=0),  # prior year
            FakePeriod(id=2, start_date=date(2026, 1, 2), period_index=1),
            FakePeriod(id=3, start_date=date(2026, 1, 16), period_index=2),
            FakePeriod(id=4, start_date=date(2026, 1, 30), period_index=3),  # current
            FakePeriod(id=5, start_date=date(2026, 2, 13), period_index=4),  # future
        ]
        transfers = [
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=1),  # 2025 — excluded
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=2),  # 2026 — included
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=3),  # 2026 — included
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=4),  # current — included
            FakeTransfer(to_account_id=10, amount=Decimal("500"), pay_period_id=5),  # future — excluded
            FakeTransfer(to_account_id=99, amount=Decimal("999"), pay_period_id=2),  # other acct — excluded
        ]
        current_period = periods[3]  # id=4

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=[],
            all_transfers=transfers,
            all_periods=periods,
            current_period=current_period,
        )

        # Periods 2, 3, 4 are in 2026 and <= current. $500 * 3 = $1500.
        assert result.ytd_contributions == Decimal("1500")

    def test_combined_deductions_and_transfers(self):
        """Periodic contribution sums both deduction and transfer amounts."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        deductions = [
            FakeDeduction(
                amount=Decimal("500.00"),
                calc_method_name="flat",
                annual_salary=Decimal("100000"),
                pay_periods_per_year=26,
            ),
        ]
        transfers = [
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=1),
            FakeTransfer(to_account_id=10, amount=Decimal("200"), pay_period_id=2),
        ]
        periods = [
            FakePeriod(id=1, start_date=date(2026, 1, 2), period_index=0),
            FakePeriod(id=2, start_date=date(2026, 1, 16), period_index=1),
        ]
        current_period = periods[0]

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=deductions,
            all_transfers=transfers,
            all_periods=periods,
            current_period=current_period,
        )

        # $500 deduction + $200 avg transfer = $700
        assert result.periodic_contribution == Decimal("700.00")

    def test_no_employer_when_type_none(self):
        """employer_params is None when type is 'none'."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type="none",
        )
        current_period = FakePeriod(id=1, start_date=date(2026, 3, 5), period_index=4)

        result = calculate_investment_inputs(
            account_id=10,
            investment_params=params,
            deductions=[],
            all_transfers=[],
            all_periods=[current_period],
            current_period=current_period,
        )

        assert result.employer_params is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_services/test_investment_projection.py -v`

Expected: ImportError — `app.services.investment_projection` does not exist yet.

**Step 3: Implement the helper**

Create `app/services/investment_projection.py`:

```python
"""
Shekel Budget App — Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, transfer, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance()."""
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    gross_biweekly: Decimal


def calculate_investment_inputs(
    account_id,
    investment_params,
    deductions,
    all_transfers,
    all_periods,
    current_period,
):
    """Compute projection inputs for an investment account.

    Args:
        account_id:        int — the investment account ID.
        investment_params:  Object with employer fields and annual_contribution_limit.
        deductions:         List of deduction-like objects with:
                            .amount, .calc_method_name, .annual_salary, .pay_periods_per_year
        all_transfers:      List of transfer-like objects with:
                            .to_account_id, .amount, .pay_period_id
        all_periods:        List of period objects with .id, .start_date, .period_index
        current_period:     The current period object.

    Returns:
        InvestmentInputs dataclass.
    """
    # Step 1: Periodic contribution from paycheck deductions.
    periodic_contribution = ZERO
    gross_biweekly = ZERO

    for ded in deductions:
        salary = Decimal(str(ded.annual_salary))
        pay_per_year = ded.pay_periods_per_year or 26
        gross = (salary / pay_per_year).quantize(TWO_PLACES)
        gross_biweekly = gross
        amt = Decimal(str(ded.amount))
        if ded.calc_method_name == "percentage":
            amt = (gross * amt).quantize(TWO_PLACES)
        periodic_contribution += amt

    # Step 2: Transfer-based contributions (average per period).
    acct_transfers = [
        t for t in all_transfers
        if t.to_account_id == account_id and not getattr(t, "is_deleted", False)
    ]
    if acct_transfers:
        total_xfer = sum(Decimal(str(t.amount)) for t in acct_transfers)
        num_periods_with_xfer = len(set(t.pay_period_id for t in acct_transfers))
        if num_periods_with_xfer > 0:
            periodic_contribution += (total_xfer / num_periods_with_xfer).quantize(
                TWO_PLACES
            )

    # Step 3: Employer params.
    employer_params = None
    emp_type = getattr(investment_params, "employer_contribution_type", "none")
    if emp_type and emp_type != "none":
        employer_params = {
            "type": emp_type,
            "flat_percentage": getattr(investment_params, "employer_flat_percentage", None) or ZERO,
            "match_percentage": getattr(investment_params, "employer_match_percentage", None) or ZERO,
            "match_cap_percentage": getattr(investment_params, "employer_match_cap_percentage", None) or ZERO,
            "gross_biweekly": gross_biweekly,
        }

    # Step 4: YTD contributions from transfers.
    ytd_contributions = ZERO
    if current_period:
        current_year = current_period.start_date.year
        ytd_period_ids = {
            p.id for p in all_periods
            if p.start_date.year == current_year
            and p.start_date <= current_period.start_date
        }
        for t in acct_transfers:
            if t.pay_period_id in ytd_period_ids:
                ytd_contributions += Decimal(str(t.amount))

    # Step 5: Annual contribution limit.
    annual_limit = getattr(investment_params, "annual_contribution_limit", None)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        employer_params=employer_params,
        annual_contribution_limit=annual_limit,
        ytd_contributions=ytd_contributions,
        gross_biweekly=gross_biweekly,
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_services/test_investment_projection.py -v`

Expected: All 10 tests PASS.

**Step 5: Commit**

```bash
git add app/services/investment_projection.py tests/test_services/test_investment_projection.py
git commit -m "feat: add shared investment projection input calculator"
```

---

### Task 2: Update savings dashboard to use full projection inputs

**Files:**
- Modify: `app/routes/savings.py`

**Step 1: Write a failing test for employer contributions**

Add to `tests/test_routes/test_savings.py`, in the helpers section:

```python
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.ref import AccountType, CalcMethod, DeductionTiming, FilingStatus


def _create_investment_account_with_contributions(seed_user, seed_periods):
    """Create a 401k with employer flat 5% and employee deduction.

    Returns:
        (Account, InvestmentParams, SalaryProfile, PaycheckDeduction)
    """
    from app.models.scenario import Scenario

    acct_type = db.session.query(AccountType).filter_by(name="401k").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name="Test 401k Employer",
        current_anchor_balance=Decimal("50000.00"),
        current_anchor_period_id=seed_periods[0].id,
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

    # Create salary profile with a deduction targeting this account.
    scenario = seed_user["scenario"]
    filing_status = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=scenario.id,
        filing_status_id=filing_status.id,
        name="Test Salary",
        annual_salary=Decimal("100000.00"),
        pay_periods_per_year=26,
        state_code="NC",
    )
    db.session.add(profile)
    db.session.flush()

    pre_tax = db.session.query(DeductionTiming).filter_by(name="pre_tax").first()
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").first()
    deduction = PaycheckDeduction(
        salary_profile_id=profile.id,
        deduction_timing_id=pre_tax.id,
        calc_method_id=flat_method.id,
        name="401k Contribution",
        amount=Decimal("500.0000"),
        target_account_id=acct.id,
    )
    db.session.add(deduction)
    db.session.commit()
    return acct, params, profile, deduction
```

Add this test to `TestDashboard`:

```python
def test_dashboard_investment_account_includes_contributions(
    self, app, auth_client, seed_user,
):
    """Investment cards include employee + employer contributions in projections."""
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

        acct, params, profile, ded = _create_investment_account_with_contributions(
            seed_user, periods,
        )

        resp = auth_client.get("/savings")
        assert resp.status_code == 200

        html = resp.data.decode()

        # With $500/period employee + 5% employer ($192.31/period) + 7% growth
        # on $50k, projections should be substantially higher than growth-only.
        # Growth-only 1yr ~$53,500. With contributions (~$18k/yr), ~$71k+.
        amounts = re.findall(r'\$([0-9,]+)', html)
        amounts_int = [
            int(a.replace(',', ''))
            for a in amounts
            if int(a.replace(',', '')) > 60000
        ]

        # With contributions, at least one projected amount should exceed $60,000.
        assert len(amounts_int) > 0, (
            "Expected at least one projected amount > $60,000 with contributions, "
            f"but found amounts: {amounts}. Contributions not being applied."
        )
```

**Step 2: Run the test to verify it fails**

Run: `pytest tests/test_routes/test_savings.py::TestDashboard::test_dashboard_investment_account_includes_contributions -v`

Expected: FAIL — contributions are not yet passed to `project_balance()`.

**Step 3: Implement full projection in savings.py**

Add the new imports at the top of `app/routes/savings.py`:

```python
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.services import amortization_engine, balance_calculator, growth_engine, pay_period_service, savings_goal_service
from app.services.investment_projection import calculate_investment_inputs
```

Add a batch query for paycheck deductions after the `investment_params_map` loading block (~line 132). Add this right after the `investment_params_map` block:

```python
    # Batch-load paycheck deductions targeting investment accounts.
    deductions_by_account = {}
    if investment_params_map:
        inv_account_ids = list(investment_params_map.keys())
        deductions = (
            db.session.query(PaycheckDeduction)
            .join(SalaryProfile)
            .filter(
                SalaryProfile.user_id == user_id,
                SalaryProfile.is_active.is_(True),
                PaycheckDeduction.target_account_id.in_(inv_account_ids),
                PaycheckDeduction.is_active.is_(True),
            )
            .all()
        )
        for ded in deductions:
            deductions_by_account.setdefault(ded.target_account_id, []).append(ded)
```

Replace the `elif acct_investment_params and current_period:` block (~lines 227-249) with:

```python
        elif acct_investment_params and current_period:
            # Investment/retirement: full growth projection with contributions.
            acct_deductions = deductions_by_account.get(acct.id, [])
            # Adapt deductions to the interface expected by the helper.
            adapted_deductions = []
            for ded in acct_deductions:
                profile = ded.salary_profile
                adapted_deductions.append(type("D", (), {
                    "amount": ded.amount,
                    "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
                    "annual_salary": profile.annual_salary,
                    "pay_periods_per_year": profile.pay_periods_per_year or 26,
                })())

            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=acct_investment_params,
                deductions=adapted_deductions,
                all_transfers=all_transfers,
                all_periods=all_periods,
                current_period=current_period,
            )

            future_periods = [
                p for p in all_periods
                if p.period_index >= current_period.period_index
            ]
            if future_periods:
                projection = growth_engine.project_balance(
                    current_balance=anchor_balance,
                    assumed_annual_return=acct_investment_params.assumed_annual_return,
                    periods=future_periods,
                    periodic_contribution=inputs.periodic_contribution,
                    employer_params=inputs.employer_params,
                    annual_contribution_limit=inputs.annual_contribution_limit,
                    ytd_contributions_start=inputs.ytd_contributions,
                )
                proj_by_idx = {
                    p.period_index: pb.end_balance
                    for pb in projection
                    for p in all_periods
                    if p.id == pb.period_id
                }
                for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
                    target_idx = current_period.period_index + offset_count
                    if target_idx in proj_by_idx:
                        projected[offset_label] = proj_by_idx[target_idx]
```

**Step 4: Run the failing test to verify it passes**

Run: `pytest tests/test_routes/test_savings.py::TestDashboard::test_dashboard_investment_account_includes_contributions -v`

Expected: PASS.

**Step 5: Run the full savings suite**

Run: `pytest tests/test_routes/test_savings.py -v`

Expected: All tests pass (21 total).

**Step 6: Commit**

```bash
git add app/routes/savings.py tests/test_routes/test_savings.py
git commit -m "feat: include contributions and employer match in savings card projections"
```

---

### Task 3: Refactor investment detail route to use shared helper

**Files:**
- Modify: `app/routes/investment.py`

This task DRYs up the investment detail route by replacing its inline contribution calculation with the shared helper. No behavior change — pure refactor.

**Step 1: Run baseline investment route tests**

Run: `pytest tests/test_routes/test_investment.py -v`

Expected: All 11 tests pass. Note the exact count.

**Step 2: Refactor investment.py to use the shared helper**

Replace the inline contribution logic (lines 60-126) with:

```python
    from app.services.investment_projection import calculate_investment_inputs

    # Find paycheck deductions targeting this account.
    deductions = (
        db.session.query(PaycheckDeduction)
        .join(SalaryProfile)
        .filter(
            SalaryProfile.user_id == current_user.id,
            SalaryProfile.is_active.is_(True),
            PaycheckDeduction.target_account_id == account_id,
            PaycheckDeduction.is_active.is_(True),
        )
        .all()
    )

    # Adapt deductions for the shared helper.
    adapted_deductions = []
    for ded in deductions:
        profile = ded.salary_profile
        adapted_deductions.append(type("D", (), {
            "amount": ded.amount,
            "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

    # Load all transfers for this account.
    period_ids = [p.id for p in all_periods]
    acct_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.to_account_id == account_id,
            Transfer.pay_period_id.in_(period_ids),
            Transfer.is_deleted.is_(False),
        )
        .all()
    ) if period_ids else []

    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=params,
        deductions=adapted_deductions,
        all_transfers=acct_transfers,
        all_periods=all_periods,
        current_period=current_period,
    )

    periodic_contribution = inputs.periodic_contribution
    employer_params = inputs.employer_params
    employer_contribution_per_period = Decimal("0")
    if employer_params:
        employer_contribution_per_period = growth_engine.calculate_employer_contribution(
            employer_params, periodic_contribution
        )

    ytd_contributions = inputs.ytd_contributions
```

Remove the now-unused `_calculate_ytd_contributions()` helper at the bottom of the file.

**Step 3: Run investment route tests**

Run: `pytest tests/test_routes/test_investment.py -v`

Expected: All 11 tests still pass (same count as baseline — pure refactor).

**Step 4: Commit**

```bash
git add app/routes/investment.py
git commit -m "refactor: use shared investment projection helper in detail route"
```

---

### Task 4: Full regression suite

**Files:** None (verification only)

**Step 1: Run all tests**

Run: `pytest --tb=short -q`

Expected: All tests pass (693 baseline + ~12 new = ~705), 0 failures.

**Step 2: Spot-check related suites**

Run: `pytest tests/test_routes/test_savings.py tests/test_routes/test_investment.py tests/test_services/test_investment_projection.py tests/test_services/test_growth_engine.py -v`

Expected: All pass.
