# Bug Investigation: Year-End Tab Shows Incorrect Account Balances

**Date:** 2026-04-11
**Status:** Investigation complete, two distinct root causes identified

## Summary

The Year-End tab on /analytics shows incorrect Dec 31 balances on both the Debt Progress
and Savings Progress cards. Two separate root causes are identified:

1. **Debt accounts (mortgage):** Dec 31 balance ($180,699.23) is HIGHER than the current
   actual balance ($178,103.41), suggesting the year-end schedule does not incorporate
   actual payment history despite code that appears to load and pass payments.

2. **Investment/retirement accounts:** The savings progress section computes the Jan 1
   starting balance using the plain balance calculator (no growth engine), understating
   the starting point for the year's growth projection. The net worth section correctly
   uses the growth engine from the anchor period, creating an inconsistency between the
   two sections on the same tab.

---

## Code State

The year-end service was refactored in two commits:

1. **35bc626** (April 9): Added `_generate_debt_schedules()` with payment preparation,
   replaced naive `calculate_balances_with_amortization()` for debt progress, added growth
   engine dispatch for savings progress.

2. **114ec45** (April 10): Threaded investment context through net worth, added
   `_build_investment_balance_map()`, removed dead amortization fallback.

Both commits are on the `dev` branch and present in the current HEAD (08ea1c3). The
implementation plans (`implementation_plan_year_end_fixes.md` and
`implementation_plan_net_worth_and_amort_cleanup.md`) describe these changes. The code
matches the plans.

---

## Root Cause 1: Debt Progress -- Dec 31 Balance Is Too High

### Symptom

The Debt Progress card shows the mortgage (account 3) with:
- Jan 1 balance: some value derived from `_balance_from_schedule_at_date(schedule, date(2025, 12, 31), original)`
- Dec 31 balance: $180,699.23
- The loan dashboard (/accounts/3/loan) and savings dashboard (/savings) both show the
  current balance as $178,103.41 (April 10, 2026)
- Since $178,103.41 < $180,699.23 and the mortgage balance must decrease over time, the
  Dec 31 projection is clearly wrong

### Code Path Trace

The year-end service computes the Dec 31 debt balance through this chain:

1. `_build_summary()` (line 207) calls `_generate_debt_schedules(ctx["debt_accounts"], scenario.id)`
2. `_generate_debt_schedules()` (line 1213) for each debt account:
   - Loads `LoanParams` (line 1238)
   - Calls `get_payment_history(account.id, scenario_id)` (line 1247)
   - Loads active `EscrowComponent` rows (line 1250)
   - Calls `compute_contractual_pi(params)` (line 1260)
   - Calls `prepare_payments_for_engine(raw_payments, ...)` (line 1261)
   - Calls `generate_schedule(current_principal=params.original_principal, ..., payments=payments if payments else None, ...)` (line 1291)
3. `_compute_debt_progress()` (line 647) calls `_balance_from_schedule_at_date(schedule, date(year, 12, 31), original)` (line 691)

The **loan dashboard** (/accounts/3/loan) uses an identical chain:

1. `_load_loan_context()` (line 329):
   - Loads active `EscrowComponent` rows (line 353)
   - Gets baseline `Scenario` (line 362)
   - Calls `get_payment_history(account.id, scenario.id)` (line 367)
   - Calls `prepare_payments_for_engine(raw_payments, ...)` (line 370)
2. `get_loan_projection(params, payments=payments, rate_changes=rate_changes)` (line 455)
   which internally calls `generate_schedule()` with the same parameters

The **savings dashboard** (/savings) uses the same chain at lines 358-415 of
`savings_dashboard_service.py`, then walks the schedule to today's date.

All three code paths call:
- `get_payment_history(account_id, scenario_id)` with the same account_id and the
  baseline scenario's id
- `prepare_payments_for_engine(raw_payments, payment_day, monthly_escrow, contractual_pi)`
  with the same prepared inputs
- `generate_schedule()` starting from `original_principal` at `origination_date` with
  `term_months` -- the same engine with the same parameters

### Why the Schedules Must Differ

If the schedules were identical, the Dec 31 balance would be LOWER than the April balance
(a standard amortizing loan always decreases with payments >= interest). The fact that
Dec 31 ($180,699.23) is $2,595.82 higher than April ($178,103.41) proves the year-end
schedule differs from the loan/savings dashboard schedules.

The $180,699.23 value is consistent with a **contractual-only schedule** (no actual
payment records). A contractual schedule replays the standard monthly P&I from origination.
If the user has made extra payments that brought the actual balance below the contractual
pace by April 2026, the contractual Dec 31 balance could plausibly be higher than the
actual April balance.

### Most Likely Cause: Empty Payment List

The critical line in `_generate_debt_schedules()` is:

```python
payments=payments if payments else None    # line 1299
```

If `prepare_payments_for_engine()` returns an empty list `[]`, this expression evaluates
to `None` (empty list is falsy in Python), and `generate_schedule()` receives
`payments=None`, producing a contractual-only schedule. This happens when
`get_payment_history()` returns an empty list.

`get_payment_history()` returns empty when no shadow income transactions match its
filters:
- `Transaction.account_id == account_id`
- `Transaction.scenario_id == scenario_id`
- `Transaction.transfer_id.isnot(None)`
- `Transaction.transaction_type_id == income_type_id` (Income)
- `Transaction.is_deleted.is_(False)`
- `Status.excludes_from_balance.is_(False)`

If the `scenario_id` passed by `_generate_debt_schedules()` does not match the
`scenario_id` on the mortgage's shadow income transactions, the query returns nothing.
Both the year-end service and the loan route derive `scenario_id` from:

```python
db.session.query(Scenario).filter_by(user_id=user_id, is_baseline=True).first()
```

These are identical queries. However, there could be a data-level issue:
- Shadow income transactions might have a different `scenario_id` (e.g., from a non-baseline
  scenario, or from before the baseline was designated)
- Transactions might be soft-deleted (`is_deleted=True`)
- Transactions might have a status with `excludes_from_balance=True`

### Alternative Cause: Escrow-Induced Negative Amortization

If payments are non-empty but `prepare_payments_for_engine()` subtracts escrow
aggressively enough to make the net payment less than the monthly interest, the schedule
would show negative amortization (increasing balance). The escrow subtraction logic caps
at `min(monthly_escrow, amount - contractual_pi)`, which should produce at least the
contractual P&I. But if `compute_contractual_pi()` returns an unreasonably low value, the
cap would be ineffective.

This is less likely because the loan dashboard uses the same preparation and produces
correct balances. But it cannot be ruled out without data-level inspection.

### What the Correct Balance Should Be

The loan dashboard and savings dashboard both show $178,103.41 at April 10, 2026. This
comes from walking the PAYMENT-AWARE amortization schedule (generated by
`get_loan_projection()` with prepared payments) to today's date. The Dec 31 balance from
the same schedule should be approximately $178,103 minus ~8 months of principal reduction
(roughly $175,000-$176,000 depending on the rate and term).

### Debugging Steps

1. **Verify payments reach the engine.** Add temporary logging in
   `_generate_debt_schedules()`:
   ```python
   logger.info("Account %d: %d raw payments, %d prepared payments",
               account.id, len(raw_payments), len(payments))
   ```
   If raw_payments is 0 for the mortgage, the issue is in `get_payment_history()`.
   If raw_payments > 0 but payments is 0, the issue is in `prepare_payments_for_engine()`.

2. **Compare scenario IDs.** Log the `scenario_id` used by the year-end service and
   verify it matches the shadow income transactions' `scenario_id`:
   ```python
   logger.info("Scenario ID: %d", scenario_id)
   ```
   Then query: `SELECT scenario_id FROM budget.transactions WHERE account_id = 3 AND transfer_id IS NOT NULL LIMIT 5;`

3. **Compare schedule balances.** If payments are non-empty, log the schedule balance at
   April 2026 and Dec 2026:
   ```python
   for row in schedule:
       if row.payment_date.month == 4 and row.payment_date.year == 2026:
           logger.info("April balance: %s, confirmed: %s", row.remaining_balance, row.is_confirmed)
       if row.payment_date.month == 12 and row.payment_date.year == 2026:
           logger.info("Dec balance: %s, confirmed: %s", row.remaining_balance, row.is_confirmed)
   ```

4. **Test the `payments if payments else None` pattern.** Change to explicit:
   ```python
   payments=payments if len(payments) > 0 else None
   ```
   This avoids the falsy-list issue. However, `prepare_payments_for_engine()` returns the
   input unchanged when it is empty (line 186: `if not payments: return payments`), so
   the empty-list scenario requires `get_payment_history()` itself to return empty.

---

## Root Cause 2: Savings Progress -- Investment/Retirement Balances Are Understated

### Symptom

Investment and retirement account balances on the Savings Progress card are lower than
expected. The Jan 1 balance does not include growth between the anchor date and Jan 1,
understating the starting point for the year's projection.

### Code Path Comparison

**Savings Progress** (`_project_investment_for_year()`, line 847):

```python
balances = _get_account_balance_map(account, scenario, all_periods)   # NO ctx
jan1_bal = _lookup_period_balance(balances, year, 1, all_periods)

projection = growth_engine.project_balance(
    current_balance=jan1_bal,          # understated starting balance
    ...
    periods=year_periods,              # only year periods
)
dec31_bal = projection[-1].end_balance
```

`_get_account_balance_map()` is called WITHOUT `ctx`, so the investment dispatch
(line 1773) is skipped. The function falls through to `calculate_balances()` (line 1787),
which returns anchor + transactions with NO growth engine. The `jan1_bal` at Dec 31 of the
prior year reflects only the anchor balance plus transaction amounts -- it does NOT include
assumed annual return or employer contributions between the anchor date and Jan 1.

**Net Worth** (`_build_investment_balance_map()`, line 1385):

```python
base_balances, _ = balance_calculator.calculate_balances(**base_args)
anchor_balance = base_balances.get(anchor_pid, ZERO)

projection = growth_engine.project_balance(
    current_balance=anchor_balance,    # correct starting balance
    ...
    periods=post_anchor,               # all post-anchor periods
)
```

The net worth section starts the growth engine from the **anchor balance** at the **anchor
period**, projecting forward through ALL post-anchor periods. The Jan 1 balance includes
growth from anchor to Jan 1 (if the anchor is before Jan 1). This is correct.

### The Discrepancy

| Section | Jan 1 starting point | Growth period | Result |
|---------|---------------------|---------------|--------|
| Net worth | Anchor balance (at anchor date) | Anchor -> all future periods | Correct: includes growth before and during year |
| Savings progress | Anchor + transactions at Jan 1 (no growth) | Jan 1 -> Dec 31 only | **Understated**: missing growth from anchor to Jan 1 |

For example, if a 401(k) has:
- Anchor balance: $48,000 (set October 2025)
- Contributions Oct-Dec 2025: $2,000
- Growth Oct 2025 - Jan 2026 at 7% annual: ~$900

The net worth section's Jan 1 balance: ~$50,900 (anchor + contributions + growth)
The savings progress section's jan1_bal: ~$50,000 (anchor + contributions, NO growth)

The $900 understatement propagates through the year, with compounding making it worse:
- Growth engine on $50,900 at 7% for 26 periods: ~$54,700 at Dec 31
- Growth engine on $50,000 at 7% for 26 periods: ~$53,700 at Dec 31
- Understatement at Dec 31: ~$1,000

### The Fix

`_project_investment_for_year()` should derive jan1_bal from a growth-aware balance map
instead of the plain balance calculator. Two approaches:

**Option A (minimal change):** Call `_get_account_balance_map()` WITH ctx so the
investment dispatch triggers `_build_investment_balance_map()`:
```python
balances = _get_account_balance_map(account, scenario, all_periods, ctx=ctx)
jan1_bal = _lookup_period_balance(balances, year, 1, all_periods)
```
Then extract the growth from Jan 1 to Dec 31 instead of re-running the growth engine.
This reuses the net worth path but requires `ctx` to be available (it already is -- passed
as a parameter to `_compute_savings_progress()` and through to `_project_investment_for_year()`).

**Option B (separate growth):** Run the growth engine from the anchor through the end of
the prior year to compute a growth-adjusted jan1_bal, then run it again for the target
year from that adjusted starting point. This is more work but keeps the savings progress
calculation self-contained.

Option A is simpler but risks double-counting: if `_get_account_balance_map()` returns
growth-projected balances and `_project_investment_for_year()` then re-runs the growth
engine for the year, the year's growth is counted in both the jan1_bal (via the full
projection) and the growth engine output. The implementation must extract jan1_bal from
the pre-generated balance map WITHOUT re-running the growth engine for the year.

### Additional Issue: ytd_contributions_start

`_project_investment_for_year()` passes `ytd_contributions_start=ZERO` (line 950) while
`_build_investment_balance_map()` passes `ytd_contributions_start=inputs.ytd_contributions`
(line 1493). For a year starting Jan 1, ZERO is correct (new calendar year, no prior
contributions). But `_build_investment_balance_map()` starts from the anchor (which may be
mid-year), so it correctly passes the YTD contributions at that point. This is not a bug
in the savings progress section -- it is correct for a Jan 1 starting point.

---

## Root Cause 3: Net Worth Debt Balances (Confirmed Correct)

The net worth section uses `_get_account_balance_map()` with `debt_schedules` (passed
through from `_build_summary()`). For debt accounts, this dispatches to
`_schedule_to_period_balance_map()` (line 1739), which maps the pre-generated amortization
schedule to pay periods. This uses the same schedule as the debt progress section.

If the debt progress schedule is contractual-only (Root Cause 1), the net worth section's
debt balances are also contractual-only. The net worth chart would overstate the mortgage
balance (show it as higher than actual), reducing net worth below its true value.

---

## Files That Would Need to Change

| File | Change | Reason |
|------|--------|--------|
| `app/services/year_end_summary_service.py` | Debug/fix `_generate_debt_schedules()` payment loading | Root Cause 1: verify payments reach the engine |
| `app/services/year_end_summary_service.py` | Fix `_project_investment_for_year()` to use growth-aware jan1_bal | Root Cause 2: understated investment starting balance |
| `tests/test_services/test_year_end_summary_service.py` | Add/update tests for both fixes | Verify correct balances |

---

## Whether This Is a Year-End Service Bug or a Data Source Bug

**Root Cause 1 (debt):** Most likely a **year-end service bug** in how payments are loaded
or passed to the engine. The data sources (shadow income transactions, escrow components,
loan params) appear to be queried identically to the loan dashboard. The most probable code
issue is the `payments if payments else None` pattern converting an empty list to `None`.
However, a data-level issue (mismatched scenario_id on transactions) cannot be ruled out
without runtime debugging.

**Root Cause 2 (investment/retirement):** A clear **year-end service bug** in
`_project_investment_for_year()`. The function computes jan1_bal from the wrong data source
(plain balance calculator instead of growth-aware projection). The data itself is correct.

---

## Relationship to Bug 1 (Amortization -- docs/bug_investigation_01_amortization.md)

Bug 1 identified that `get_loan_projection()` started schedules from today instead of
origination, making past confirmed payments invisible. The fix (commit d2455e8) changed
the function to start from `original_principal` at `origination_date` with `term_months`.

**The year-end service was the correct reference implementation at the time of Bug 1.**
Bug investigation 01, section 9, explicitly notes that `_generate_debt_schedules()` (lines
1291-1301) "calls the same engine correctly" and was used as the model for fixing
`get_loan_projection()`.

**The current year-end issues are NOT downstream consequences of Bug 1:**

- **Root Cause 1 (debt):** The year-end service has always called `generate_schedule()`
  with the correct origination parameters. The current issue is about whether actual
  payment records are reaching the engine, not about the schedule's starting point. Bug 1's
  fix does not affect this.

- **Root Cause 2 (investment/retirement):** This is an investment-account-specific issue in
  the savings progress section. It has no connection to amortization or Bug 1.

**These are independent bugs that share no root cause with Bug 1.** They existed before
Bug 1 was found and were not introduced by the Bug 1 fix. The year-end fixes (commits
35bc626 and 114ec45) addressed the original year-end bugs (naive balance calculator for
debt, naive formula for savings) but introduced or left behind the two issues documented
here.

---

## Tests That Should Verify the Fixes

### Root Cause 1 (Debt)

| ID | Test | Setup | Expected |
|----|------|-------|----------|
| 1 | test_debt_progress_with_extra_payments | Mortgage with confirmed payments > contractual | Dec 31 balance < contractual Dec 31 balance; reflects actual payment amounts |
| 2 | test_debt_progress_payments_reach_engine | Mortgage with known payments | Schedule rows for months with payments show `is_confirmed=True`; balance trajectory matches payment-aware amortization |
| 3 | test_debt_progress_empty_payments_uses_contractual | Mortgage with no shadow income | Schedule is contractual-only; Dec 31 balance equals contractual amortization |
| 4 | test_debt_balance_decreases_monotonically | Any mortgage with payments >= interest | Every month's balance is <= the prior month's balance |

### Root Cause 2 (Investment/Retirement)

| ID | Test | Setup | Expected |
|----|------|-------|----------|
| 5 | test_savings_progress_investment_jan1_includes_growth | 401(k) with anchor in prior year | jan1_bal includes growth from anchor to Jan 1; higher than anchor + contributions alone |
| 6 | test_savings_progress_consistent_with_net_worth | 401(k) with anchor in prior year | Savings progress Jan 1 balance for the account matches the net worth section's implicit Jan 1 balance for the same account |
| 7 | test_savings_progress_investment_dec31_includes_full_year_growth | 401(k) $10k, 7% return | Dec 31 balance reflects 7% return on the growth-adjusted jan1_bal, not on the understated plain-calculator jan1_bal |
| 8 | test_savings_progress_investment_with_employer | 401(k) with flat 3% employer match | employer_contributions > 0; dec31_bal reflects both personal + employer contributions + growth |

---

## Summary of Findings

| Account Type | Section | Status | Root Cause | Severity |
|-------------|---------|--------|------------|----------|
| Mortgage | Debt Progress | Bug | Payment records likely not reaching the amortization engine (empty list -> None conversion, or data mismatch) | High -- shows wrong balance, wrong principal paid |
| Mortgage | Net Worth | Bug (downstream) | Same schedule as Debt Progress; if schedule is contractual-only, net worth overstates debt | High -- net worth is wrong |
| Investment/Retirement | Savings Progress | Bug | jan1_bal from plain balance calculator, missing pre-year growth | Medium -- understates balances |
| Investment/Retirement | Net Worth | Correct | Uses `_build_investment_balance_map()` with growth engine from anchor | N/A |
| Interest-bearing (HYSA) | Savings Progress | Correct | Uses `calculate_balances_with_interest()` | N/A |
| Interest-bearing (HYSA) | Net Worth | Correct | Uses `calculate_balances_with_interest()` | N/A |
| Plain savings | Savings Progress | Correct | Uses `calculate_balances()` | N/A |
