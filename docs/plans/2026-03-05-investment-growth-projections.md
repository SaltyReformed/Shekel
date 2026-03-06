# Investment Growth Projections on Savings Dashboard

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make savings dashboard account cards show compound-growth-projected balances for investment/retirement accounts, matching what the investment detail charts already show.

**Architecture:** Mirror the existing debt-account pattern in `savings.py` — when an account has `InvestmentParams`, bypass the flat `balance_calculator.calculate_balances()` and instead use `growth_engine.project_balance()` to compute the 3-month, 6-month, and 1-year milestone projections. The `current_balance` stays as the anchor (real balance); only `projected` changes.

**Tech Stack:** Flask, SQLAlchemy, growth_engine.py (pure functions), pytest

---

## Context

### Root Cause

`app/routes/savings.py` lines 168-233 have three code paths for computing projected balances:

| Account Type | Method | Growth? |
|---|---|---|
| HYSA | `calculate_balances_with_interest()` | Yes |
| Mortgage/Auto Loan | `amortization_engine` | Yes |
| Investment/Retirement | `calculate_balances()` | **No** |

Investment accounts fall through to the plain balance calculator which only sums transactions/transfers. The growth engine (`growth_engine.project_balance()`) is only called on the investment detail page (`investment.py:138`), not on the savings dashboard cards.

### Fix Location

Only one production file changes: `app/routes/savings.py`. The fix adds a third special-case block (after debt accounts, before the generic fallback) that uses the growth engine for accounts with `InvestmentParams`.

### Key References

- **Growth engine API:** `app/services/growth_engine.py:73-172` — `project_balance()` returns `List[ProjectedBalance]` with `.end_balance` per period
- **Investment route example:** `app/routes/investment.py:128-155` — shows how to call `project_balance()` with contributions, employer params, etc.
- **Debt account pattern:** `app/routes/savings.py:198-224` — the pattern we're mirroring (special-case block that replaces `projected` dict)
- **Savings test helpers:** `tests/test_routes/test_savings.py` — `_create_savings_account()`, `_create_goal()` patterns
- **Investment test helpers:** `tests/test_routes/test_investment.py` — `_create_investment_account()`, `_create_investment_params()` patterns

---

### Task 1: Write failing test — investment account card shows growth projections

**Files:**
- Modify: `tests/test_routes/test_savings.py`

**Step 1: Write the failing test**

Add a helper and test to `tests/test_routes/test_savings.py`:

```python
# Add these imports at the top (alongside existing ones):
from app.models.investment_params import InvestmentParams

# Add this helper after the existing helpers:

def _create_investment_account_with_params(seed_user, seed_periods):
    """Create a 401k account with investment params and anchor period.

    Returns:
        (Account, InvestmentParams)
    """
    acct_type = db.session.query(AccountType).filter_by(name="401k").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name="Test 401k",
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
        employer_contribution_type="none",
    )
    db.session.add(params)
    db.session.commit()
    return acct, params
```

Add this test to the `TestDashboard` class:

```python
def test_dashboard_investment_account_shows_growth_projections(
    self, app, auth_client, seed_user, seed_periods,
):
    """Investment account cards show projected balances with compound growth."""
    with app.app_context():
        acct, params = _create_investment_account_with_params(
            seed_user, seed_periods,
        )

        resp = auth_client.get("/savings")
        assert resp.status_code == 200

        # The card should show "Projected:" with milestone labels.
        assert b"Projected:" in resp.data or b"projected" in resp.data.lower()

        # With 7% annual return on $50k, the 1-year projection should
        # be notably higher than $50,000. If growth is NOT applied,
        # the balance stays flat at $50,000 (the bug).
        # At 7% on $50k with no contributions, ~$53,500 after 1 year.
        # Check that the projected section contains a value > 50,000.
        html = resp.data.decode()

        # Find all projected dollar amounts in the response.
        # The "1 year" projection should show growth beyond $50,000.
        import re
        # Look for dollar amounts in the projected section pattern: $XX,XXX
        amounts = re.findall(r'\$([0-9,]+)', html)
        amounts_int = [int(a.replace(',', '')) for a in amounts if int(a.replace(',', '')) > 50000]

        # With 7% growth, at least one projected amount should exceed $50,000.
        assert len(amounts_int) > 0, (
            "Expected at least one projected amount > $50,000 with 7% growth, "
            "but all amounts were <= $50,000. Growth is not being applied."
        )
```

**Step 2: Run the test to verify it fails**

Run: `pytest tests/test_routes/test_savings.py::TestDashboard::test_dashboard_investment_account_shows_growth_projections -v`

Expected: FAIL — the current code uses flat `calculate_balances()` so projected amounts won't exceed $50,000.

**Step 3: Commit the failing test**

```bash
git add tests/test_routes/test_savings.py
git commit -m "test: add failing test for investment growth on savings cards"
```

---

### Task 2: Implement growth-projected milestones for investment accounts

**Files:**
- Modify: `app/routes/savings.py:150-252` (the account_data loop)

**Step 1: Add the growth_engine import**

At the top of `app/routes/savings.py`, add to the service imports:

```python
from app.services import amortization_engine, balance_calculator, growth_engine, pay_period_service, savings_goal_service
```

(Just add `growth_engine` to the existing import line.)

**Step 2: Add investment growth projection block**

In the `dashboard()` function, find the block at ~line 226 that computes `projected` for non-loan accounts:

```python
        else:
            for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
                if current_period:
                    target_idx = current_period.period_index + offset_count
                    for p in all_periods:
                        if p.period_index == target_idx and p.id in balances:
                            projected[offset_label] = balances[p.id]
                            break
```

Replace it with:

```python
        elif acct_investment_params and current_period:
            # Investment/retirement: use growth engine for compound growth projections.
            future_periods = [
                p for p in all_periods
                if p.period_index >= current_period.period_index
            ]
            if future_periods:
                projection = growth_engine.project_balance(
                    current_balance=anchor_balance,
                    assumed_annual_return=acct_investment_params.assumed_annual_return,
                    periods=future_periods,
                )
                # Build a period_index → end_balance map from projection results.
                proj_by_idx = {}
                for pb in projection:
                    for p in all_periods:
                        if p.id == pb.period_id:
                            proj_by_idx[p.period_index] = pb.end_balance
                            break
                for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
                    target_idx = current_period.period_index + offset_count
                    if target_idx in proj_by_idx:
                        projected[offset_label] = proj_by_idx[target_idx]
        else:
            for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
                if current_period:
                    target_idx = current_period.period_index + offset_count
                    for p in all_periods:
                        if p.period_index == target_idx and p.id in balances:
                            projected[offset_label] = balances[p.id]
                            break
```

**Important:** The `acct_investment_params` variable is currently assigned AFTER the projected block (line 243). Move it before the projected computation. Find the line:

```python
        acct_investment_params = investment_params_map.get(acct.id)
```

And move it to just before the `if acct_loan_params:` block (~line 198), so it's available for the `elif` check.

**Step 3: Run the test to verify it passes**

Run: `pytest tests/test_routes/test_savings.py::TestDashboard::test_dashboard_investment_account_shows_growth_projections -v`

Expected: PASS

**Step 4: Run the full savings test suite**

Run: `pytest tests/test_routes/test_savings.py -v`

Expected: All tests pass (no regressions).

**Step 5: Commit**

```bash
git add app/routes/savings.py
git commit -m "feat: apply compound growth to investment account cards on savings dashboard"
```

---

### Task 3: Run full test suite to verify no regressions

**Files:** None (verification only)

**Step 1: Run all tests**

Run: `pytest --tb=short -q`

Expected: All 674+ tests pass, 0 failures.

**Step 2: Spot-check related tests**

Run: `pytest tests/test_routes/test_savings.py tests/test_routes/test_investment.py tests/test_services/test_growth_engine.py -v`

Expected: All pass.
